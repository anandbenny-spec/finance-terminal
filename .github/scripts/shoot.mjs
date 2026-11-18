// One 1440 x 900 picture of a public page through headless Chrome's DevTools protocol (no dependency: Node's built-in
// WebSocket). Used by the screenshots workflow (#200). Arguments: url, output file, optional element id to scroll to.
// Prints nothing about the page itself. Reads a public page as an anonymous visitor.
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, out, anchor] = process.argv.slice(2);
const chrome = process.env.CHROME_BIN;
const port = 9222 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "shot-"))}`, "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
try {
  let target;
  for (let i = 0; i < 40 && !target; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((t) => t.type === "page");
    } catch { /* not up yet */ }
    if (!target) await sleep(500);
  }
  if (!target) throw new Error("Chrome did not start");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const waiting = new Map();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
  };
  const send = (method, params = {}) => new Promise((res) => { const n = ++id; waiting.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url });
  await sleep(12000); // the page loads its data in the browser: give it time
  if (anchor) {
    const r = await send("Runtime.evaluate", {
      expression: `(() => { const e = document.getElementById(${JSON.stringify(anchor)}); if (!e) return false; e.scrollIntoView({ block: "start" }); return true; })()`,
      returnByValue: true,
    });
    if (r.result?.result?.value !== true) throw new Error("anchor not found");
    await sleep(2000);
  }
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  ws.close();
} finally {
  proc.kill();
}
