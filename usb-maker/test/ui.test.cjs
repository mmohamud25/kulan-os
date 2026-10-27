// Kulan USB Maker's window in a browser, with Tauri stood in for: every
// screen, the checks before making a stick, and what's asked of the Rust side.
//   node test/ui.test.cjs            (SHOTS=dir for screenshots)
"use strict";
const { chromium } = require("playwright");
const path = require("path");
const assert = require("assert");

const MOCK = `
window.__calls = [];
window.__listeners = {};
window.__drives = [];
window.__TAURI__ = {
  core: { invoke: async (cmd, args) => {
    window.__calls.push([cmd, args]);
    if (cmd === "list_drives") return window.__drives;
    if (cmd === "start") {
      const send = (p) => (window.__listeners.progress || []).forEach((f) => f({ payload: p }));
      const steps = window.__fail
        ? [{ phase: "release" }, { phase: "error", text: "The USB stick isn't plugged in any more." }]
        : [{ phase: "release" }, { phase: "download", done: 5e8, total: 1.9e9 }, { phase: "verify", done: 1e9, total: 1.9e9 },
           { phase: "permission" }, { phase: "write", done: 1e9, total: 1.9e9 }, { phase: "check", done: 1.9e9, total: 1.9e9 },
           { phase: "done" }];
      window.__send = send;
      window.__steps = steps;
      return null;
    }
    return null;
  } },
  event: { listen: async (name, f) => { (window.__listeners[name] ||= []).push(f); } },
  dialog: { open: async () => "/home/amina/Downloads/kulan-os-amd64.iso" },
};`;

(async () => {
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 560, height: 820 }, colorScheme: scheme });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.addInitScript(MOCK);
    await page.goto("file://" + path.resolve(__dirname, "../ui/index.html"));
    const shot = async (name) => process.env.SHOTS && page.screenshot({ path: `${process.env.SHOTS}/${name}-${scheme}.png`, fullPage: true });

    // No stick: waiting for one, nothing can start.
    await page.waitForSelector("#drives .empty");
    assert.strictEqual(await page.isDisabled("#make"), true);
    assert.match(await page.textContent("#why-not"), /Plug in a USB stick/);
    await shot("empty");

    // Sticks appear (one too small, and the computer's own disks are never sent by Rust).
    await page.evaluate(() => { window.__drives = [{ id: "disk4", path: "/dev/rdisk4", name: "SanDisk Ultra", size: 32e9 },
                                                    { id: "disk5", path: "/dev/rdisk5", name: "Old stick", size: 2e9 }]; });
    await page.waitForSelector("#drives input[value=disk4]", { timeout: 5000 });
    assert.strictEqual(await page.isDisabled("#drives input[value=disk5]"), true, "a 2 GB stick can't be chosen");
    assert.strictEqual(await page.isChecked("#drives input[value=disk4]"), true, "the one usable stick is chosen");
    assert.strictEqual(await page.isDisabled("#make"), false);

    // The boot key for the chosen make.
    await page.selectOption("#brand", "lenovo-thinkpad");
    assert.match(await page.textContent("#key"), /F12/);

    // An .iso file instead: needs one chosen.
    await page.check("input[name=source][value=file]");
    assert.strictEqual(await page.isDisabled("#make"), true);
    await page.click("#pick-file");
    await page.waitForFunction(() => document.getElementById("file-name").textContent === "kulan-os-amd64.iso");
    assert.strictEqual(await page.isDisabled("#make"), false);
    await page.check("input[name=source][value=amd64]");

    // An organisation's code: checked before anything starts.
    await page.click("#org-box summary");
    await page.fill("#org-code", "nope");
    assert.strictEqual(await page.isDisabled("#make"), true);
    assert.match(await page.textContent("#org-note"), /ABCD-EFGH-JKLM/);
    await page.fill("#org-code", "abcd-efgh-jk2m");
    assert.strictEqual(await page.isDisabled("#make"), false);
    await shot("choose");

    // Erasing is asked first; Cancel changes nothing.
    await page.click("#make");
    await page.waitForSelector("#confirm[open]");
    assert.match(await page.textContent("#confirm-text"), /SanDisk Ultra.*32 GB/);
    await shot("confirm");
    await page.click("#confirm-no");
    assert.strictEqual(await page.evaluate(() => window.__calls.filter((c) => c[0] === "start").length), 0);

    // Making it: what's asked of Rust, and every phase shown.
    await page.click("#make");
    await page.click("#confirm-yes");
    await page.waitForSelector("#working:not(.hidden)");
    const start = await page.evaluate(() => window.__calls.find((c) => c[0] === "start")[1]);
    assert.deepStrictEqual(start, { source: { kind: "download", arch: "amd64" }, drive: "disk4",
      organisation: { fleet: { server: "https://xegnwqxpegznsufulclx.supabase.co/functions/v1/fleet", code: "ABCD-EFGH-JK2M" } } });
    const texts = [];
    for (let i = 0; i < 6; i++) {
      await page.evaluate((i) => window.__send(window.__steps[i]), i);
      texts.push(await page.textContent("#work-text"));
      if (i === 1) await shot("downloading");
      if (i === 4) await shot("writing");
    }
    assert.match(texts[1], /Downloading: 0\.5 GB of 1\.9 GB/);
    assert.match(texts[3], /password|Yes/);
    assert.match(texts[4], /Writing \(52%\)/);
    assert.match(texts[5], /Reading it back \(100%\)/);
    await page.evaluate(() => window.__send(window.__steps[6]));
    await page.waitForSelector("#done:not(.hidden)");
    const next = await page.textContent("#next-steps");
    assert.match(next, /F12/);
    assert.match(next, /joins your organisation/);
    await shot("done");

    // Stop asks Rust to stop.
    await page.click("#again");
    await page.click("#make");
    await page.click("#confirm-yes");
    await page.click("#stop");
    assert.ok(await page.evaluate(() => window.__calls.some((c) => c[0] === "stop")));
    await page.evaluate(() => window.__send({ phase: "stopped" }));
    await page.waitForSelector("#choose:not(.hidden)");

    // A failure says what happened, and Try again goes back.
    await page.evaluate(() => { window.__fail = true; });
    await page.click("#make");
    await page.click("#confirm-yes");
    await page.evaluate(() => { window.__send(window.__steps[0]); window.__send(window.__steps[1]); });
    await page.waitForSelector("#failed:not(.hidden)");
    assert.match(await page.textContent("#failed-text"), /isn't plugged in/);
    await shot("failed");
    await page.click("#retry");
    await page.waitForSelector("#choose:not(.hidden)");

    // The stick unplugged: no longer chosen.
    await page.evaluate(() => { window.__drives = []; });
    await page.waitForSelector("#drives .empty", { timeout: 5000 });
    assert.strictEqual(await page.isDisabled("#make"), true);

    // No sideways scrolling at the narrowest window.
    await page.setViewportSize({ width: 440, height: 600 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 440));
    assert.deepStrictEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log("usb maker window: every screen passed");
})().catch((e) => { console.error(e); process.exit(1); });
