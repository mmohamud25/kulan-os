// Kulan USB Maker's window. The work is done in Rust (src-tauri); this asks
// for it with invoke() and follows it through "progress" events.
"use strict";

const T = window.__TAURI__;
const invoke = (cmd, args) => T.core.invoke(cmd, args);
const $ = (id) => document.getElementById(id);
const GB = 1e9;
const MIN_SIZE = 4 * GB;
// Kulan Fleet's server: computers installed from an organisation's stick join it.
const FLEET_SERVER = "https://xegnwqxpegznsufulclx.supabase.co/functions/v1/fleet";
const JOIN_CODE = /^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/;

// The key that opens the start-up menu, by make.
const BRANDS = [
  ["", "Choose…", ""],
  ["acer", "Acer", "Tap F12 as it starts. If nothing happens, turn on “F12 Boot Menu” in its settings (F2) first."],
  ["asus", "ASUS", "Tap Esc as it starts (F8 on some desktops)."],
  ["dell", "Dell", "Tap F12 as it starts."],
  ["framework", "Framework", "Tap F12 as it starts."],
  ["hp", "HP", "Tap Esc as it starts, then press F9."],
  ["lenovo-thinkpad", "Lenovo ThinkPad", "Tap F12 as it starts (or Enter, then F12)."],
  ["lenovo", "Lenovo IdeaPad or Legion", "Tap F12 as it starts. On some, switch it off and press the small Novo button instead."],
  ["msi", "MSI", "Tap F11 as it starts."],
  ["samsung", "Samsung", "Tap Esc (or F10) as it starts."],
  ["surface", "Microsoft Surface", "Switch it off. Hold the volume-down button, press and release the power button, and keep holding volume-down until the logo appears."],
  ["toshiba", "Toshiba or Dynabook", "Tap F12 as it starts."],
  ["mac", "Intel Mac", "Hold the Option (⌥) key as it starts, and choose “EFI Boot”."],
  ["other", "Something else", "Tap F12, Esc, F11 or F9 as it starts: one of these opens the start-up menu on most computers."],
];

let drives = [];
let picked = { drive: null, file: null };
let agreed = null;  // the drive as it was when Erase was clicked
let total = 0;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

const size = (n) => n >= 10 * GB ? `${Math.round(n / GB)} GB` : `${(n / GB).toFixed(1)} GB`;

// --- 1. the stick -----------------------------------------------------------------------
async function refreshDrives() {
  let found;
  try {
    found = await invoke("list_drives");
  } catch (e) {
    found = [];
    console.warn(e);
  }
  const same = found.length === drives.length && found.every((d, i) => d.id === drives[i].id && d.size === drives[i].size);
  drives = found;
  if (!same || !$("drives").children.length) renderDrives();
}

function renderDrives() {
  const box = $("drives");
  if (picked.drive && !drives.some((d) => d.id === picked.drive)) picked.drive = null;
  // One stick plugged in: chosen for you, unless it's big enough to be a backup drive.
  const usable = drives.filter((d) => d.size >= MIN_SIZE);
  if (!picked.drive && usable.length === 1 && usable[0].size <= 256 * GB) picked.drive = usable[0].id;
  if (!drives.length) {
    box.replaceChildren(el("div", { class: "empty" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      "Plug in a USB stick. It appears here."));
  } else {
    box.replaceChildren(...drives.map((d) => {
      const small = d.size < MIN_SIZE;
      const big = d.size > 256 * GB;
      const input = el("input", { type: "radio", name: "drive", value: d.id, disabled: small, checked: picked.drive === d.id,
                                  onchange: () => { picked.drive = d.id; update(); } });
      return el("label", { class: `row${small ? " off" : ""}` }, input,
        el("span", { class: "grow" }, el("b", {}, d.name),
          el("small", {}, small ? `${size(d.size)} · Too small: use one of 4 GB or more`
                                : big ? `${size(d.size)} · A big drive: make sure it’s the right one` : size(d.size))));
    }));
  }
  update();
}

// --- 2. Kulan OS ------------------------------------------------------------------------
function source() {
  const v = document.querySelector("input[name=source]:checked").value;
  return v === "file" ? { kind: "file", path: picked.file } : { kind: "download", arch: v };
}

async function pickFile() {
  const path = await T.dialog.open({ multiple: false, directory: false,
                                     filters: [{ name: "Kulan OS", extensions: ["iso"] }] });
  if (typeof path === "string" && path) {
    picked.file = path;
    $("file-name").textContent = path.split(/[\\/]/).pop();
    document.querySelector("input[name=source][value=file]").checked = true;
  }
  update();
}

function organisation() {
  const code = $("org-code").value.trim().toUpperCase();
  if (!code) return { value: null };
  if (!JOIN_CODE.test(code)) return { problem: "A join code looks like ABCD-EFGH-JKLM." };
  return { value: { fleet: { server: FLEET_SERVER, code } } };
}

// --- 3. the computer --------------------------------------------------------------------
function showKey() {
  const brand = BRANDS.find((b) => b[0] === $("brand").value);
  $("key").textContent = brand && brand[2] ? `To start it from the USB stick: ${brand[2]}` : "";
}

// --- making -----------------------------------------------------------------------------
function problem() {
  if (!drives.length) return "Plug in a USB stick first.";
  if (!picked.drive) return "Choose the USB stick.";
  if (source().kind === "file" && !picked.file) return "Choose the .iso file.";
  return organisation().problem || "";
}

function update() {
  const why = problem();
  $("why-not").textContent = why;
  $("org-note").textContent = organisation().problem || "";
  $("org-note").classList.toggle("bad", !!organisation().problem);
  $("make").disabled = !!why;
}

function askFirst() {
  const d = drives.find((x) => x.id === picked.drive);
  if (!d || problem()) return;
  agreed = d;
  $("confirm-text").textContent = `Everything on “${d.name}” (${size(d.size)}) will be erased. This can’t be undone.`;
  $("confirm").showModal();
}

async function make() {
  $("confirm").close();
  show("working");
  setStep("get");
  setBar(null);
  $("work-text").textContent = "Starting…";
  $("stop").disabled = false;
  try {
    await invoke("start", { source: source(), drive: agreed.id, fingerprint: agreed.fingerprint,
                            organisation: organisation().value });
  } catch (e) {
    failed(String(e));
  }
}

function show(which) {
  for (const id of ["choose", "working", "done", "failed"]) $(id).classList.toggle("hidden", id !== which);
}

function setStep(step) {
  const order = ["get", "write", "check"];
  for (const li of document.querySelectorAll(".steps li")) {
    const i = order.indexOf(li.dataset.step), now = order.indexOf(step);
    li.classList.toggle("done", i < now);
    li.classList.toggle("now", i === now);
  }
}

function setBar(fraction) {
  const fill = $("bar-fill");
  fill.classList.toggle("busy", fraction == null);
  if (fraction != null) fill.style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
}

let speed = { at: 0, done: 0, rate: 0 };
function rateText(done, all) {
  const now = Date.now();
  if (now - speed.at > 2000) {
    if (speed.at && done > speed.done) speed.rate = (done - speed.done) / ((now - speed.at) / 1000);
    speed = { at: now, done, rate: speed.rate };
  }
  if (!speed.rate || !all) return "";
  const left = Math.max(0, (all - done) / speed.rate);
  return left > 90 ? ` · about ${Math.round(left / 60)} minutes left` : " · less than 2 minutes left";
}

function onProgress({ phase, done, total: all, text }) {
  const pct = all ? ` (${Math.floor((done / all) * 100)}%)` : "";
  switch (phase) {
    case "release":
      setStep("get"); setBar(null);
      $("work-title").textContent = "Getting Kulan OS";
      $("work-text").textContent = "Finding the newest Kulan OS…";
      break;
    case "download":
      setStep("get"); setBar(all ? done / all : null);
      $("work-text").textContent = `Downloading: ${size(done)} of ${size(all)}${rateText(done, all)}`;
      break;
    case "verify":
      setStep("get"); setBar(all ? done / all : null);
      $("work-text").textContent = `Checking the download${pct}`;
      break;
    case "permission":
      setStep("write"); setBar(null);
      $("work-title").textContent = "Writing the USB stick";
      $("work-text").textContent = navigator.userAgent.includes("Windows")
        ? "Windows asks whether to allow Kulan USB Maker to make changes. Click Yes."
        : "Enter your computer’s password when it asks, so Kulan USB Maker can write to the stick.";
      speed = { at: 0, done: 0, rate: 0 };
      break;
    case "prepare":
      setStep("write"); setBar(null);
      $("work-text").textContent = "Getting the USB stick ready…";
      break;
    case "write":
      setStep("write"); setBar(all ? done / all : null);
      $("work-text").textContent = `Writing${pct}${rateText(done, all)}`;
      break;
    case "check":
      setStep("check"); setBar(all ? done / all : null);
      $("work-title").textContent = "Checking the USB stick";
      $("work-text").textContent = `Reading it back${pct}`;
      $("stop").disabled = false;
      break;
    case "done":
      finished();
      break;
    case "stopped":
      show("choose");
      refreshDrives();
      break;
    case "error":
      failed(text);
      break;
  }
}

function finished() {
  const brand = BRANDS.find((b) => b[0] === $("brand").value);
  const key = brand && brand[2] ? brand[2] : "Tap its start-up menu key as it starts (often F12, Esc or F9).";
  const org = organisation().value;
  $("next-steps").replaceChildren(
    el("li", {}, "Plug the USB stick into the computer you’re installing on."),
    el("li", {}, `Restart it. ${key}`),
    el("li", {}, "Choose the USB stick, then click Install Kulan OS."),
    ...(org ? [el("li", {}, "After installing, the computer joins your organisation by itself the first time it’s online.")] : []));
  show("done");
}

function failed(text) {
  $("failed-text").textContent = text || "Something went wrong.";
  show("failed");
}

function start() {
  $("brand").replaceChildren(...BRANDS.map(([v, t]) => el("option", { value: v }, t)));
  $("brand").addEventListener("change", showKey);
  $("pick-file").addEventListener("click", (e) => { e.preventDefault(); pickFile(); });
  for (const r of document.querySelectorAll("input[name=source]")) r.addEventListener("change", update);
  $("org-code").addEventListener("input", update);
  $("make").addEventListener("click", askFirst);
  $("confirm-no").addEventListener("click", () => $("confirm").close());
  $("confirm-yes").addEventListener("click", make);
  $("stop").addEventListener("click", () => { $("stop").disabled = true; $("work-text").textContent = "Stopping…"; invoke("stop"); });
  $("again").addEventListener("click", () => { show("choose"); refreshDrives(); });
  $("retry").addEventListener("click", () => { show("choose"); refreshDrives(); });
  T.event.listen("progress", (e) => onProgress(e.payload));
  renderDrives();
  refreshDrives();
  // Not while asking to erase: what's asked about stays what's erased.
  setInterval(() => { if (!$("choose").classList.contains("hidden") && !$("confirm").open) refreshDrives(); }, 2000);
}

start();
