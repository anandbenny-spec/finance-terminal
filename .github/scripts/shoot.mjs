// Capture one public page at 1440 x 900. Arguments: URL, output PNG, required panel ID, optional "scroll".
import { spawn } from "node:child_process";
import { on, once } from "node:events";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const [url, out, panel, scroll] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "shot-"));
const chrome = spawn(process.env.CHROME_BIN, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
  "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
], { stdio: "ignore" });
const closed = new Promise((resolve) => chrome.once("close", resolve));

async function pageTarget() {
  for (let attempt = 0; attempt < 40; attempt++) {
    if (chrome.exitCode !== null || chrome.signalCode) throw new Error("Chrome exited during startup");
    try {
      const [port] = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n");
      const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1000) });
      const target = (await response.json()).find((entry) => entry.type === "page");
      if (target) return target;
    } catch { /* Chrome publishes the port and page target during startup. */ }
    await sleep(500);
  }
  throw new Error("Chrome did not expose a page target");
}

// Runs inside Chrome. A completed navigation can still show the app's loading fallback.
async function preparePage(url, panel, scroll) {
  if (location.href !== url || document.readyState !== "complete") return false;
  const hasError = () => /Couldn't load|Update delayed/.test(document.body.innerText);
  if (hasError()) throw new Error("Page showed an error state");
  const heading = document.getElementById(panel);
  if (!heading?.getBoundingClientRect().height) return false;
  await document.fonts.ready;
  if (scroll === "scroll") {
    heading.style.scrollMarginTop = "112px";
    heading.scrollIntoView({ block: "start", behavior: "instant" });
  }
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  if (hasError()) throw new Error("Page showed an error state");
  return true;
}

let socket;
try {
  await once(chrome, "spawn");
  const target = await pageTarget();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await once(socket, "open", { signal: AbortSignal.timeout(5000) });

  let id = 0;
  async function send(method, params) {
    const requestId = ++id;
    const messages = on(socket, "message", { signal: AbortSignal.timeout(30000) });
    try {
      socket.send(JSON.stringify({ id: requestId, method, params }));
      for await (const [event] of messages) {
        const reply = JSON.parse(event.data);
        if (reply.id !== requestId) continue;
        if (reply.error) throw new Error(`${method}: ${reply.error.message}`);
        return reply.result;
      }
    } finally {
      await messages.return();
    }
  }

  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  const navigation = await send("Page.navigate", { url });
  if (navigation.errorText) throw new Error(`Navigation failed: ${navigation.errorText}`);

  const expression = `(${preparePage})(...${JSON.stringify([url, panel, scroll])})`;
  const deadline = Date.now() + 30000;
  while (true) {
    const reply = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description ?? "Page readiness check failed");
    if (reply.result.value === true) break;
    if (Date.now() >= deadline) throw new Error(`Page did not render panel ${panel}`);
    await sleep(250);
  }

  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(out, Buffer.from(shot.data, "base64"));
} finally {
  socket?.close();
  chrome.kill();
  await closed;
  rmSync(profile, { recursive: true, force: true, maxRetries: 3 });
}
