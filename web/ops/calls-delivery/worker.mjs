// Run from web/ against the immutable build also used by the visitor server.
import { Client } from "pg";
import { Worker } from "node:worker_threads";
import { setTimeout as delay } from "node:timers/promises";
import { installReadTrace } from "../../lib/calls-delivery/trace.ts";
import { anonymousApi, planScope, startRenderer } from "./render.mjs";
import { claimWorker, extendAttempt, finishAttempt, pendingScopes, publish, releaseWorker, startAttempt } from "./queue.mjs";

const build = process.env.CALLS_BUILD_ID;
const connectionString = process.env.CALLS_WORKER_DATABASE_URL;
const renderMs = Number(process.env.CALLS_RENDER_TIMEOUT_MS ?? 45_000);
const leaseMs = renderMs + 5_000;
if (!build || !connectionString || !Number.isSafeInteger(renderMs) || renderMs < 1_000 || renderMs > 120_000) {
  throw Error("set CALLS_BUILD_ID, CALLS_WORKER_DATABASE_URL and a valid preparation deadline");
}

const log = (event, fields = {}) => console.info(JSON.stringify({ phase: "calls-preparation", event, build, ...fields }));
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => { stopping = true; });

// The watchdog has a separate event loop. It kills the process that actually
// owns Next and the render session, including if the render itself stalls.
const watchdog = new Worker(new URL("./watchdog.mjs", import.meta.url));
watchdog.on("error", () => process.kill(process.pid, "SIGKILL"));
const read = anonymousApi();
const db = new Client({
  connectionString, application_name: "calls-artifact-worker", connectionTimeoutMillis: 4_000, statement_timeout: 10_000,
});
db.on("error", () => process.kill(process.pid, "SIGKILL"));

let renderer;
const retries = new Map();
try {
  await db.connect();
  renderer = await startRenderer(build);
  log("worker-start");
  while (!stopping) {
    if (!(await claimWorker(db))) {
      await delay(500);
      continue;
    }
    let didWork;
    try {
      didWork = await runPending();
    } finally {
      await releaseWorker(db);
    }
    await delay(didWork ? 0 : 500);
  }
} finally {
  if (renderer) await renderer.close();
  await db.end();
  await watchdog.terminate();
}

async function runPending() {
  const task = (await pendingScopes(db, build)).find((row) => {
    const retry = retries.get(row.scope);
    return !retry || retry.key !== taskKey(row) || retry.at <= Date.now();
  });
  const attempt = task && (await startAttempt(db, leaseMs));
  if (!attempt) return false;
  const { token, valid_until: validUntil } = attempt;

  try {
    const planId = `${build}/${token}:plan`;
    log("plan-start", { token, scope: task.scope, requestId: planId });
    const plan = await guarded(token, () => installReadTrace().run(
      { requestId: planId, phase: "preparation" }, () => planScope(read, task.scope),
    ));
    const parts = [];
    for (const [path, expected] of plan.routes) {
      if (stopping) return true;
      parts.push(...await guarded(token, async () => {
        const started = performance.now();
        const requestId = `${build}/${token}:${path}`;
        process.env.CALLS_PREPARATION_REQUEST_ID = requestId;
        log("render-start", { token, path, requestId });
        try {
          return await renderer.render(path, expected, requestId);
        } finally {
          delete process.env.CALLS_PREPARATION_REQUEST_ID;
          log("render-end", { token, path, requestId, elapsedMs: Math.round(performance.now() - started) });
        }
      }));
    }
    if (stopping) return true;
    const installed = await guarded(token, () => publish(db, build, task, token, { ...plan, parts, validUntil }));
    log(installed ? "publish" : "superseded", {
      token, scope: task.scope, revision: task.revision, sharedRevision: task.shared_revision,
      paths: plan.routes.length, bytes: parts.reduce((size, part) => size + part.body.length, 0),
    });
    retries.delete(task.scope);
  } catch (error) {
    const prior = retries.get(task.scope);
    const key = taskKey(task);
    const count = prior?.key === key ? prior.count + 1 : 1;
    retries.set(task.scope, { key, count, at: Date.now() + Math.min(30_000, 500 * 2 ** Math.min(count, 6)) });
    log("retry", { token, scope: task.scope, error: error.message });
  } finally {
    await finishAttempt(db, token);
  }
  return true;
}

function taskKey(task) {
  return [task.revision, task.shared_revision, task.list_revision].join("/");
}

async function guarded(token, operation) {
  // Arm the local watchdog before extending the DB expiry. Takeover is
  // delayed past this deadline even if the worker loses its DB connection.
  watchdog.postMessage(performance.now() + renderMs);
  try {
    await extendAttempt(db, token, leaseMs);
    return await operation();
  } finally {
    watchdog.postMessage(null);
  }
}
