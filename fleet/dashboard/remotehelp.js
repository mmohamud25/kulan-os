// Remote help (Kulan Fleet 2.9a), the admin's side: asks the computer
// (fleet-admin help-start), follows the session every second (help-signal),
// and when the person there allows it shows their screen (WebRTC, straight
// from the computer, or through Cloudflare's relay). With their permission,
// the mouse and keyboard over the picture go to the computer.
//
//   const help = new RemoteHelp({ api, deviceId, control, video, onStatus, onControl, onEnd });
//   await help.start();  ...  help.end();
//
// api(body) is the dashboard's call to fleet-admin. Nothing of the screen
// passes through Kulan Fleet's server, and nothing is kept.

const STATES = {
  requested: "Waiting for the computer to pick the request up…",
  asking: "Asking the person at the computer…",
  allowed: "Allowed. Connecting…",
  connected: "Connected",
};
const ENDED = { declined: "They said no.", ended: "Remote help ended.", expired: "Remote help timed out.", failed: "Remote help couldn't connect." };
const BUTTON = { 0: 0, 1: 1, 2: 2 };

export function statusText(r, hostname) {
  if (r.state === "requested") return `Waiting for ${hostname || "the computer"} to pick the request up…`;
  if (r.state === "asking") return `Asking ${r.who || "the person at the computer"} on ${hostname || "the computer"}…`;
  if (ENDED[r.state]) return `${ENDED[r.state]}${r.detail ? ` (${r.detail})` : ""}`;
  return STATES[r.state] || r.state;
}

// Where a pointer is on the picture, 0..1 each way (the picture is letterboxed
// in the element: object-fit contain). null outside the picture.
export function pointOnPicture(ev, video) {
  const box = video.getBoundingClientRect();
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh || !box.width || !box.height) return null;
  const scale = Math.min(box.width / vw, box.height / vh);
  const w = vw * scale, h = vh * scale;
  const left = box.left + (box.width - w) / 2, top = box.top + (box.height - h) / 2;
  const x = (ev.clientX - left) / w, y = (ev.clientY - top) / h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x: Math.round(x * 10000) / 10000, y: Math.round(y * 10000) / 10000 };
}

export class RemoteHelp {
  constructor({ api, deviceId, hostname, control, video, onStatus, onControl, onEnd }) {
    Object.assign(this, { api, deviceId, hostname, askControl: !!control, video, onStatus: onStatus || (() => {}),
                          onControl: onControl || (() => {}), onEnd: onEnd || (() => {}) });
    this.since = 0;
    this.outgoing = [];
    this.answer = null;
    this.state = null;
    this.control = null;  // not known yet
    this.useControl = true;
    this.over = false;
    this.listeners = [];
    this.held = new Map();  // keys and buttons sent as down: let go of when control stops or the page loses focus
  }

  async start() {
    const r = await this.api({ op: "help-start", device_id: this.deviceId, control: this.askControl });
    this.sid = r.session_id;
    this.ice = Array.isArray(r.ice) ? r.ice : [];
    this.relay = r.relay === true;
    this.onStatus(r.signed_in === false
      ? `Waiting for ${this.hostname || "the computer"}… Nobody is signed in, and someone needs to be there to allow it.`
      : statusText({ state: "requested" }, this.hostname));
    this.timer = setTimeout(() => this.poll(), 1000);
    return r;
  }

  async poll() {
    if (this.over || this.ending) return;
    const body = { op: "help-signal", session_id: this.sid, since: this.since };
    if (this.answer) body.answer = this.answer;
    const sent = this.outgoing.splice(0, 30);
    if (sent.length) body.candidates = sent;
    let r;
    try {
      r = await this.api(body);
    } catch (e) {
      this.outgoing.unshift(...sent);
      if (/no such session/i.test(e.message)) { this.finish("Remote help ended."); return; }
      if (/not now/i.test(e.message)) this.answer = null;  // the computer already has an answer
      this.timer = setTimeout(() => this.poll(), 3000);
      return;
    }
    if (this.over || this.ending) return;
    if (body.answer) this.answer = null;
    if (ENDED[r.state]) { this.finish(statusText(r, this.hostname)); return; }
    if (r.state !== this.state) {
      this.state = r.state;
      if (r.state !== "connected" || this.pc?.connectionState === "connected") this.onStatus(statusText(r, this.hostname));
    }
    if (r.control !== this.control) {
      this.control = r.control === true;
      this.onControl(this.control);
    }
    try {
      if (r.offer && !this.pc) await this.takeOffer(r.offer);
      if (this.pc?.remoteDescription) {
        for (const c of r.candidates || []) {
          if (c?.candidate) await this.pc.addIceCandidate({ candidate: c.candidate, sdpMLineIndex: c.sdpMLineIndex ?? 0 }).catch(() => {});
        }
        this.since += (r.candidates || []).length;
      }
    } catch (e) {
      console.error("Kulan Fleet remote help:", e);
      this.finish(e instanceof TypeError ? "Remote help couldn’t connect. Check your internet connection and try again."
        : "Remote help couldn’t connect. Try again in a minute.", true);
      return;
    }
    this.timer = setTimeout(() => this.poll(), this.state === "connected" && this.pc?.connectionState === "connected" ? 3000 : 1000);
  }

  async takeOffer(offer) {
    const pc = this.pc = new RTCPeerConnection({ iceServers: this.ice });
    pc.ontrack = (ev) => {
      this.video.srcObject = ev.streams[0] || new MediaStream([ev.track]);
      this.video.play?.().catch(() => {});
    };
    pc.onicecandidate = (ev) => {
      if (ev.candidate?.candidate) {
        this.outgoing.push({ candidate: ev.candidate.candidate, sdpMid: ev.candidate.sdpMid, sdpMLineIndex: ev.candidate.sdpMLineIndex });
      }
    };
    pc.ondatachannel = (ev) => { this.channel = ev.channel; };
    pc.onconnectionstatechange = () => {
      if (this.over) return;
      if (pc.connectionState === "connected") this.onStatus("Connected");
      else if (pc.connectionState === "disconnected") this.onStatus("Connection lost. Reconnecting…");
      else if (pc.connectionState === "failed") {
        this.finish(this.relay ? "Remote help couldn't connect." :
          "Remote help couldn't connect. The computers can't reach each other directly, and the relay isn't set up.", true);
      }
    };
    await pc.setRemoteDescription(offer);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.answer = { type: "answer", sdp: pc.localDescription.sdp };
    this.attachInput();
  }

  // The mouse and keyboard, over the picture: only when the person allowed it (the computer checks too).
  send(msg) {
    if (!(this.control && this.useControl && this.channel?.readyState === "open")) return;
    const id = msg.t === "down" || msg.t === "up" ? `b${msg.b}` : msg.t === "kd" || msg.t === "ku" ? `k${msg.code}` : null;
    if (id && (msg.t === "down" || msg.t === "kd")) this.held.set(id, msg);
    else if (id) this.held.delete(id);
    this.channel.send(JSON.stringify(msg));
  }

  letGo() {
    for (const msg of this.held.values()) {
      const up = msg.t === "down" ? { t: "up", b: msg.b } : { t: "ku", code: msg.code };
      if (this.channel?.readyState === "open") this.channel.send(JSON.stringify(up));
    }
    this.held.clear();
  }

  attachInput() {
    const v = this.video;
    const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); this.listeners.push([target, type, fn, opts]); };
    let queued = null;
    on(v, "pointermove", (ev) => {
      const p = pointOnPicture(ev, v);
      if (!p) return;
      if (!queued) requestAnimationFrame(() => { if (queued) this.send({ t: "move", ...queued }); queued = null; });
      queued = p;
    });
    on(v, "pointerdown", (ev) => {
      if (!(ev.button in BUTTON) || !this.control || !this.useControl) return;
      const p = pointOnPicture(ev, v);
      if (!p) return;
      ev.preventDefault();
      v.focus();
      v.setPointerCapture?.(ev.pointerId);  // the button is let go of even outside the picture
      this.send({ t: "move", ...p });
      this.send({ t: "down", b: BUTTON[ev.button] });
    });
    on(v, "pointerup", (ev) => { if (ev.button in BUTTON) this.send({ t: "up", b: BUTTON[ev.button] }); });
    on(v, "contextmenu", (ev) => { if (this.control && this.useControl) ev.preventDefault(); });
    let wheel = 0;
    on(v, "wheel", (ev) => {
      if (!this.control || !this.useControl) return;
      ev.preventDefault();
      wheel += ev.deltaMode === 1 ? ev.deltaY * 33 : ev.deltaY;
      const steps = Math.trunc(wheel / 100);
      if (steps) { wheel -= steps * 100; this.send({ t: "wheel", dx: 0, dy: Math.max(-10, Math.min(10, steps)) }); }
    }, { passive: false });
    const key = (down) => (ev) => {
      if (!this.control || !this.useControl || !ev.code) return;
      ev.preventDefault();
      if (down && ev.repeat) return;  // a held key repeats on the computer by itself
      this.send({ t: down ? "kd" : "ku", code: ev.code });
    };
    on(v, "keydown", key(true));
    on(v, "keyup", key(false));
    // Whatever is still down when the picture loses focus, or the page is hidden, is let go of on the computer.
    on(v, "blur", () => this.letGo());
    on(v, "pointercancel", () => this.letGo());
    on(document, "visibilitychange", () => { if (document.hidden) this.letGo(); });
  }

  setUseControl(on) {
    if (!on) this.letGo();
    this.useControl = !!on;
  }

  finish(text, tellServer = false) {
    if (this.over) return;
    this.over = true;
    clearTimeout(this.timer);
    this.letGo();
    for (const [t, type, fn, opts] of this.listeners) t.removeEventListener(type, fn, opts);
    this.listeners = [];
    try { this.pc?.close(); } catch { /* closed already */ }
    if (this.video) this.video.srcObject = null;
    if (tellServer && this.sid) this.api({ op: "help-end", session_id: this.sid }).catch(() => {});
    this.onEnd(text);
  }

  async end() {
    if (this.over || this.ending) return;
    this.ending = true;
    clearTimeout(this.timer);
    // The server first, so the computer knows it was ended, not broken.
    if (this.sid) await this.api({ op: "help-end", session_id: this.sid }).catch(() => {});
    this.finish("Remote help ended.");
  }
}
