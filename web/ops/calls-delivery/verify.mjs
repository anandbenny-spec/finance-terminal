// Run from web/ with the committed fixture installed in a fresh local database.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";
import { gunzipSync } from "node:zlib";
import { Client } from "pg";
import { validateEarningsList } from "./render.mjs";
import { createRequire } from "node:module";
import { chromium } from "playwright-core";

function localUrl(setting, protocol, user) {
  const url = new URL(process.env[setting] ?? "");
  assert.equal(url.protocol, protocol);
  assert.equal(url.hostname, "127.0.0.1");
  if (user) {
    assert.equal(url.username, user);
    assert.equal(url.pathname, "/calls620");
  }
  return url.href;
}

const admin = localUrl("CALLS_VERIFY_DATABASE_URL", "postgresql:", "postgres");
const workerDb = localUrl("CALLS_WORKER_DATABASE_URL", "postgresql:", "calls_worker_test");
const readerDb = localUrl("CALLS_READER_DATABASE_URL", "postgresql:", "calls_reader_test");
const postgrest = localUrl("CALLS_VERIFY_POSTGREST_URL", "http:");
const apiPort = process.env.CALLS_VERIFY_API_PORT ?? "44722";
const webPort = process.env.CALLS_VERIFY_WEB_PORT ?? "44723";
const origin = `http://127.0.0.1:${webPort}`;
const build = `calls620-local-${Date.now()}`;
const fixture = "/companies/calls-fixture/calls";
const control = "/companies/calls-control/calls";
const empty = "/companies/calls-empty/calls";
const quarter = `${fixture}/q1-fy27`;
const list = "/earnings";
const rsc = { RSC: "1" };
const quote = "fixture evidence quote";
const figure = "Fixture evidence figure";
const evidence = { build, startedAt: new Date().toISOString(), checks: [], requests: [], events: [], maxActiveRenders: 0 };
const active = new Map();
const processes = [];
const errors = [];
let dataReads = 0;
let hold;
let breakFigures = false;
let evidenceSegment;

const api = createServer(async (req, res) => {
  try {
    const path = req.url.slice("/rest/v1".length);
    assert(req.url.startsWith("/rest/v1/"));
    const target = new URL(path, postgrest);
    const isFixtureRender = req.headers["x-calls-preparation"]?.endsWith(`:${fixture}`);
    dataReads++;
    if (breakFigures && isFixtureRender && target.pathname === "/call_figures") {
      res.writeHead(500, { "Content-Type": "application/json" }).end(JSON.stringify({ message: "fixture read failure" }));
      return;
    }
    const headers = new Headers(req.headers);
    headers.delete("authorization"); // PostgREST's configured default is anon.
    headers.delete("host");
    const upstream = await fetch(target, { headers });
    const bytes = Buffer.from(await upstream.arrayBuffer());
    if (hold && isFixtureRender && target.pathname === "/citation_pages") {
      const pending = hold;
      hold = undefined;
      pending.reached.resolve(req.headers["x-calls-preparation"]);
      await pending.release.promise;
    }
    const output = new Headers(upstream.headers);
    output.delete("content-encoding");
    output.set("content-length", String(bytes.length));
    res.writeHead(upstream.status, Object.fromEntries(output)).end(bytes);
  } catch (error) {
    res.writeHead(500).end("Fixture API unavailable");
    errors.push(error.message);
  }
});

const db = new Client({ connectionString: admin });
const childEnv = {
  ...process.env,
  CALLS_BUILD_ID: build,
  NEXT_PUBLIC_SUPABASE_URL: `http://127.0.0.1:${apiPort}`,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "local-fixture-uses-postgrest-anon",
  NEXT_TELEMETRY_DISABLED: "1",
  CALLS_RENDER_TIMEOUT_MS: "6500",
};
delete childEnv.CALLS_PRIVATE_RENDERER;
delete childEnv.CALLS_PREPARATION_REQUEST_ID;
delete childEnv.CALLS_VERIFY_DATABASE_URL;

function start(name, args, env) {
  const child = spawn(process.execPath, args, { env, stdio: ["ignore", "pipe", "pipe"] });
  processes.push(child);
  for (const stream of [child.stdout, child.stderr]) {
    createInterface({ input: stream }).on("line", (line) => {
      let parsed;
      try { parsed = JSON.parse(line); } catch { /* Next diagnostics are plain text. */ }
      const at = new Date().toISOString();
      if (parsed?.phase) evidence.events.push({ at, process: name, ...parsed });
      else if (line.trim()) errors.push(`${name}: ${line}`);
      if (parsed?.event === "render-start") {
        assert(!active.has(name), "worker started a second render");
        active.set(name, parsed.requestId);
        evidence.maxActiveRenders = Math.max(evidence.maxActiveRenders, active.size);
      }
      if (parsed?.event === "render-end") active.delete(name);
    });
  }
  child.on("exit", (code, signal) => {
    evidence.events.push({ at: new Date().toISOString(), process: name, event: "exit", code, signal, interrupted: active.get(name) });
    active.delete(name);
  });
  return child;
}

const nextScript = "node_modules/next/dist/bin/next";
const startWorker = (name) => start(name, ["ops/calls-delivery/worker.mjs"], {
  ...childEnv, CALLS_WORKER_DATABASE_URL: workerDb, CALLS_READER_DATABASE_URL: "",
});

async function stop(child, signal = "SIGTERM") {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const done = once(child, "exit");
  child.kill(signal);
  await done;
}

async function until(fn, timeout = 20_000) {
  const expiry = performance.now() + timeout;
  while (performance.now() < expiry) {
    const value = await fn();
    if (value) return value;
    await delay(40);
  }
  throw Error(`Timed out. Recent events: ${JSON.stringify(evidence.events.filter((e) => e.event).slice(-9))}`);
}

async function request(phase, path, { headers = {}, status = 200, expect: text, noEvidence = false, expectRsc = false } = {}) {
  const started = Date.now();
  const response = await fetch(origin + path, { headers, redirect: "manual", cache: "no-store" });
  const body = await response.text();
  const record = {
    phase, path, headers, status: response.status, elapsedMs: Date.now() - started,
    cache: response.headers.get("cache-control"), id: response.headers.get("x-calls-request-id"),
    renderId: response.headers.get("x-calls-render-id"), sha256: response.headers.get("x-calls-sha256"),
    completedAt: response.headers.get("x-calls-completed-at"), location: response.headers.get("location"),
    contentType: response.headers.get("content-type"), evidenceBytes: body.includes(quote) || body.includes(figure),
  };
  evidence.requests.push(record);
  assert.equal(response.status, status, path);
  assert.match(record.cache ?? "", /no-store/);
  if (status === 200) {
    assert.equal(createHash("sha256").update(body).digest("hex"), record.sha256, path);
    assert(record.id && record.renderId && Date.parse(record.completedAt) <= started, path);
    assert.doesNotMatch(body, /couldn(?:['’]|&#x27;|&apos;)t load/i, path);
  }
  if (expectRsc) assert.match(record.contentType, /^text\/x-component/);
  if (text) assert.match(body, text);
  if (noEvidence) assert(!record.evidenceBytes, path);
  return { record, body };
}

function check(label) {
  evidence.checks.push(label);
  console.log(`PASS ${label}`);
}

async function ready(scope) {
  return until(async () => (await db.query(`
    select render_token from calls_delivery.current_manifest where build_id = $1 and scope = $2
  `, [build, scope])).rows[0]?.render_token);
}

async function allReady() {
  for (const scope of ["@earnings", "calls-fixture", "calls-control", "calls-empty"]) await ready(scope);
}

async function firstWithdrawn(phase) {
  const variants = [
    [fixture, {}], [quarter, {}], [quarter + "?_rsc=fresh", rsc],
    [quarter + "?_rsc=segment", { ...rsc, "Next-Router-Segment-Prefetch": "/_tree", "Next-Router-Prefetch": "1" }],
    [quarter + ".rsc", rsc], [list, {}], [list + "?_rsc=fresh", rsc],
    [fixture, { Host: `localhost:${webPort}` }],
  ];
  if (evidenceSegment) variants.push([quarter, { ...rsc, "Next-Router-Segment-Prefetch": evidenceSegment }]);
  await Promise.all(variants.map(([path, headers]) => request(phase, path, { headers, status: 503, noEvidence: true })));
}

async function withAnonymous(sql) {
  await db.query("begin read only");
  try {
    await db.query("set local role anon");
    return (await db.query(sql)).rows;
  } finally {
    await db.query("rollback");
  }
}

function holdNextCitation() {
  const reached = Promise.withResolvers();
  const release = Promise.withResolvers();
  hold = { reached, release };
  return hold;
}

const eventSource = (closed) => db.query("update core.source set is_suppressed = $1 where key = 'calls_fixture_event'", [closed]);
const documentSource = (closed) => db.query("update core.source set is_suppressed = $1 where key = 'calls_fixture_document'", [closed]);
const fieldGate = (closed) => db.query("update core.field_policy set status = $1 where field_key = 'ai.guidance_extraction'", [closed ? "hidden" : "labelled"]);
const onlyCalls = "select call_slug, is_default, is_newest from api.earnings_calls where company_slug = 'calls-fixture' order by call_slug";

try {
  await db.connect();
  assert.equal((await db.query("select count(*)::int as n from core.source where key like 'calls_fixture_%' and not is_suppressed")).rows[0].n, 2);
  assert.equal((await db.query("select status from core.field_policy where field_key = 'ai.call_summary'")).rows[0].status, "hidden");
  api.listen(Number(apiPort), "127.0.0.1");
  await once(api, "listening");
  const compile = start("build", [nextScript, "build"], { ...childEnv, CALLS_WORKER_DATABASE_URL: "", CALLS_READER_DATABASE_URL: "" });
  const [code] = await once(compile, "exit");
  assert.equal(code, 0, errors.slice(-20).join("\n"));
  start("reader", [nextScript, "start", "--hostname", "127.0.0.1", "--port", webPort], {
    ...childEnv, CALLS_READER_DATABASE_URL: readerDb, CALLS_WORKER_DATABASE_URL: "",
  });
  await until(async () => fetch(origin + "/robots.txt").then(() => true, () => false));

  const before = dataReads;
  await firstWithdrawn("unprepared");
  await request("unprepared spoofed preparation header", fixture, { status: 503, headers: { "x-calls-prepare": "visitor" } });
  await delay(100);
  assert.equal(dataReads, before);
  check("Unprepared visits returned 503 across HTML and navigation and issued zero Data API reads");

  let worker = startWorker("worker-a");
  await allReady();
  const original = await withAnonymous(onlyCalls);
  assert.deepEqual(original, [
    { call_slug: "q1-fy27", is_default: true, is_newest: false },
    { call_slug: "q2-fy27", is_default: false, is_newest: true },
  ]);
  await request("older default", fixture, { expect: /Q1 FY27 call: what was said/ });
  await request("quarter Flight", quarter, { headers: rsc, expect: /fixture evidence quote/, expectRsc: true });
  const segments = (await db.query(`
    select variant, body from calls_delivery.current_response
    where build_id = $1 and path = $2 and variant like 'segment:%'
  `, [build, quarter])).rows;
  evidenceSegment = segments.find((row) => gunzipSync(row.body).includes(quote))?.variant.slice("segment:".length);
  assert(evidenceSegment, "quarter evidence must be present in a prepared navigation segment");
  await request("quarter evidence segment", quarter, {
    headers: { ...rsc, "Next-Router-Segment-Prefetch": evidenceSegment }, expect: /fixture evidence quote/, expectRsc: true,
  });
  await request("older default segment", fixture, { headers: { ...rsc, "Next-Router-Segment-Prefetch": "/_tree" }, expectRsc: true });
  const earnings = await request("earnings newest", list, { expect: /Q2 FY27/ });
  const { parse } = createRequire(import.meta.url)("next/dist/compiled/node-html-parser");
  const listDoc = parse(earnings.body);
  const listOracle = await withAnonymous("select * from api.earnings_latest where company_slug in (select slug from api.companies where market='IN')");
  validateEarningsList(listDoc, listOracle);
  const withChange = (change) => listOracle.map((row) => row.company_slug === "calls-fixture" ? { ...row, ...change } : row);
  assert.throws(() => validateEarningsList(listDoc, withChange({ fiscal_label: "Q1 FY27" })), /incorrect earnings list row/);
  assert.throws(() => validateEarningsList(listDoc, withChange({ status: "none_found" })), /incorrect earnings list row/);
  assert.throws(() => validateEarningsList(listDoc, listOracle.slice(1)), /incomplete earnings list/);
  check("Earnings validation rejects stale quarter, stale status and a roster mismatch");
  await request("empty call list", empty, { expect: /No transcript found/ });
  const legacy = await fetch(origin + "/companies/calls-fixture?tab=calls&call=q1-fy27", { redirect: "manual" });
  assert.equal(legacy.status, 307);
  assert.equal(new URL(legacy.headers.get("location"), origin).pathname, quarter);
  const unknown = await request("valid-shaped unknown call fallback", `${fixture}/unknown`, { status: 307 });
  assert.equal(new URL(unknown.record.location, origin).pathname, fixture);
  await request("invalid path", `${fixture}/Q1_FY27`, { status: 404 });
  await request("unknown company", "/companies/calls-missing/calls", { status: 404 });
  check("Anonymous oracle and prepared pages identify an older default, the newest list row, the empty state and redirects");

  const browser = await chromium.launch({
    executablePath: process.env.CALLS_VERIFY_CHROME,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.route((url) => url.origin !== origin, (route) => route.abort());
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const apiBefore = dataReads;
    await page.goto(origin + fixture);
    await page.getByRole("button", { name: /show the cited passage/ }).first().click();
    await page.getByText(/fixture evidence quote/).waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    const nextQuarter = page.waitForResponse((response) =>
      response.url().includes(`${fixture}/q2-fy27`) && response.request().headers().rsc === "1");
    await page.locator(`a[href="${fixture}/q2-fy27"]`).click();
    const navigation = await nextQuarter;
    assert.equal(navigation.status(), 200);
    assert((await navigation.allHeaders())["x-calls-render-id"]);
    await page.getByRole("heading", { name: "Q2 FY27 call: what was said" }).waitFor();
    assert.equal(await page.getByRole("link", { name: "Q2 FY27", exact: true }).getAttribute("aria-current"), "true");
    assert.deepEqual(pageErrors, []);
    assert.equal(dataReads, apiBefore);
    check("Chrome hydrated the citation chip and switched quarters over prepared Next Flight with zero Data API reads");
  } finally {
    await browser.close();
  }

  const untouched = (await request("control before writes", control)).record;
  const revisions = () => db.query("select scope, revision from calls_delivery.revision order by scope").then((r) => r.rows);
  const beforeRevisions = await revisions();
  const reads = dataReads;
  await db.query("update ops.job_run set finished_at = clock_timestamp() where id = (select min(id) from ops.job_run)");
  assert.deepEqual(await revisions(), beforeRevisions);
  const stable = (await request("after unrelated write", control)).record;
  assert.equal(stable.renderId, untouched.renderId);
  assert.equal(stable.sha256, untouched.sha256);
  assert.equal(dataReads, reads);
  check("A committed unrelated job write preserved every revision, the render ID and the response digest");

  await stop(worker);
  await db.query("update core.event set status = 'stored' where slug = 'q2-fy27' and event_type = 'earnings_call'");
  const shifted = await withAnonymous(onlyCalls);
  assert.equal(shifted.find((row) => row.is_default).call_slug, "q2-fy27");
  await firstWithdrawn("default shift");
  assert.equal((await request("scoped default shift control", control)).record.sha256, untouched.sha256);
  worker = startWorker("worker-b");
  await allReady();
  await request("default shift rebuilt", fixture, { expect: /Q2 FY27 call: what was said/ });
  check("Changing is_default without adding a call rebuilt the affected default, quarters and list while the control stayed ready");

  await stop(worker);
  await db.query("update core.event set is_published = true where slug = 'q3-fy27' and event_type = 'earnings_call'");
  await firstWithdrawn("publication");
  assert.equal((await request("scoped publication control", control)).record.sha256, untouched.sha256);
  worker = startWorker("worker-c");
  await allReady();
  await request("publication rebuilt default", fixture, { expect: /Q3 FY27 call: what was said/ });
  await request("publication rebuilt quarter", fixture + "/q3-fy27", { expect: /Q3 FY27 call: what was said/ });
  await request("publication rebuilt list", list, { expect: /Q3 FY27/ });
  check("Publishing a stored quarter queued and automatically replaced that company and the earnings list");

  for (const [label, change] of [["event-only suppression", eventSource], ["document-only suppression", documentSource], ["field gate closure", fieldGate]]) {
    await stop(worker);
    await request(`${label} witness`, quarter, { expect: /fixture evidence quote/ });
    const readsBefore = dataReads;
    await change(true);
    await firstWithdrawn(label);
    assert.equal(dataReads, readsBefore);
    const rows = await withAnonymous("select metric_label from api.call_figures where company_slug = 'calls-fixture'");
    assert.deepEqual(rows, []);
    worker = startWorker(`worker ${label}`);
    await allReady();
    await request(`${label} rebuilt`, fixture, { noEvidence: true });
    await request(`${label} rebuilt Flight`, fixture, { headers: rsc, expectRsc: true, noEvidence: true });
    await request(`${label} rebuilt list`, list, { noEvidence: true });
    await change(false);
    await allReady();
    await request(`${label} reopened`, quarter, { expect: /fixture evidence quote/ });
    check(`${label}: first reads withdrew HTML, Flight and segments, then the worker rebuilt and reopened on committed state`);
  }

  breakFigures = true;
  await db.query("update equity.stated_figure set metric_text = 'Retry witness' where metric_label = $1", [figure]);
  const seenRetry = await until(() => evidence.events.find((e) => e.event === "retry" && e.scope === "calls-fixture"));
  await request("failed panel 200 rejected", fixture, { status: 503, noEvidence: true });
  breakFigures = false;
  await allReady();
  check(`A failed panel stayed unready and retried automatically (attempt ${seenRetry.token})`);

  const stale = holdNextCitation();
  await db.query("update equity.stated_figure set metric_text = 'Stale witness' where metric_label = $1", [figure]);
  const staleId = await Promise.race([stale.reached.promise, delay(8_000).then(() => { throw Error("stale attempt never started"); })]);
  const standby = startWorker("standby");
  await until(() => evidence.events.find((e) => e.process === "standby" && e.event === "worker-start"));
  await delay(650);
  assert(!evidence.events.some((e) => e.process === "standby" && e.event === "render-start"));
  await fieldGate(true);
  await firstWithdrawn("stale worker during gate closure");
  stale.release.resolve();
  const staleToken = staleId.split("/")[1].split(":")[0];
  await until(() => evidence.events.find((e) => e.event === "superseded" && e.token === staleToken));
  assert.equal((await db.query("select count(*)::int as n from calls_delivery.manifest where build_id = $1 and render_token = $2", [build, staleToken])).rows[0].n, 0);
  await allReady();
  await request("stale worker fenced", fixture, { noEvidence: true });
  check("Standby could not render concurrently and the stale worker could not install a revoked revision");
  await stop(standby);
  await fieldGate(false);
  await allReady();

  const interrupted = holdNextCitation();
  await db.query("update equity.stated_figure set metric_text = 'Interrupt witness' where metric_label = $1", [figure]);
  const killedId = await Promise.race([interrupted.reached.promise, delay(8_000).then(() => { throw Error("interrupted attempt never started"); })]);
  await documentSource(true);
  await stop(worker, "SIGKILL");
  interrupted.release.resolve();
  const killedToken = killedId.split("/")[1].split(":")[0];
  await firstWithdrawn("worker killed after document suppression");
  const slot = (await db.query("select expires_at from calls_delivery.render_slot where token = $1", [killedToken])).rows[0];
  assert(slot && slot.expires_at.getTime() > Date.now());
  worker = startWorker("restarted");
  await allReady();
  const restarted = evidence.events.find((e) => e.process === "restarted" && e.event === "render-start");
  assert(restarted && Date.parse(restarted.at) >= slot.expires_at.getTime());
  assert.equal((await db.query("select count(*)::int as n from calls_delivery.manifest where build_id = $1 and render_token = $2", [build, killedToken])).rows[0].n, 0);
  await request("restart stays withdrawn", fixture, { noEvidence: true });
  await documentSource(false);
  await allReady();
  await request("restart reopened after committed restoration", quarter, { expect: /fixture evidence quote/ });
  check("SIGKILL interrupted an active render, the replacement waited for the old deadline and reconciled the durable queue");

  await stop(worker);
  // These changes belong only to the disposable database. Check both owners
  // and the unaffected control, before starting the next worker.
  await db.query(`
    insert into core.event(event_type,entity_id,slug,title,starts_on,status,source_id,is_published)
    select 'corporate_action',e.id,'verify-split','Fixture split','2026-11-25','effective',s.id,true
    from core.entity e,core.source s where e.slug='calls-fixture' and s.key='nse';
    insert into equity.corporate_action(event_id,kind,ex_date,subject,exchange)
    select id,'split','2026-11-25','Fixture split','NSE' from core.event where slug='verify-split';
  `);
  const scopeRev = async (scope) => (await db.query("select revision from calls_delivery.revision where scope=$1", [scope])).rows[0].revision;
  const controlRev = await scopeRev("calls-control");
  const beforeAction = await scopeRev("calls-fixture");
  await db.query("update equity.corporate_action set ex_date='2026-11-26' where subject='Fixture split'");
  const afterAction = await scopeRev("calls-fixture");
  assert.notEqual(beforeAction, afterAction);
  await db.query(`insert into equity.adjustment_factor(event_id, entity_id, ex_date, method, status)
    select id,entity_id,'2026-11-26','reviewed','held' from core.event where slug='verify-split'`);
  assert.notEqual(await scopeRev("calls-fixture"), afterAction);
  assert.equal(await scopeRev("calls-control"), controlRev);
  check("Corporate-action corrections and adjustment factors advance only the owning company revision");

  await db.query(`insert into core.task_freshness(task_key,label,status,last_success_at,stale_after)
    select key,'Fixture clock','ok',clock_timestamp(),interval '10 seconds' from ops.task order by key limit 1
    on conflict(task_key) do update set label=excluded.label,status='ok',
      last_success_at=excluded.last_success_at,stale_after=excluded.stale_after`);
  worker = startWorker("clock");
  await allReady();
  await stop(worker);
  const clock = await db.query(`select m.valid_until = t.last_success_at + t.stale_after as matches
    from calls_delivery.current_manifest m,core.task_freshness t
    where m.build_id=$1 and m.scope='@earnings' and t.label='Fixture clock'`, [build]);
  assert.equal(clock.rows[0]?.matches, true);
  await until(async () => (await withAnonymous("select status from api.task_freshness where label='Fixture clock'"))[0]?.status === "stale");
  await firstWithdrawn("clock deadline without a write");
  worker = startWorker("clock rebuild");
  await allReady();
  await request("clock rebuilt", quarter, { expect: /fixture evidence quote/ });
  await stop(worker);
  check("Crossing a freshness deadline withdraws HTML and navigation without a write and queues replacements");

  const outbound = evidence.events.filter((e) => e.phase === "calls-api-outbound" && e.process !== "build");
  const workIds = new Set(evidence.events.filter((e) => ["plan-start", "render-start"].includes(e.event)).map((e) => e.requestId));
  assert(outbound.length > 0 && outbound.every((e) => e.origin === "preparation" && workIds.has(e.requestId)));
  // A cold, ordinary page on the same reader process provides an independent
  // transport control. The guard never needs to make a deliberate Data API call.
  const beforeProbe = dataReads;
  await fetch(origin + "/companies/calls-fixture/financials").then((r) => r.arrayBuffer());
  assert(dataReads > beforeProbe);
  await until(() => evidence.events.some((e) => e.process === "reader" && e.phase === "calls-api-outbound" && e.origin === "unattributed"));
  check("Transport tracing detects a real public-server read and attributes guarded visits zero Data API reads");
  assert.equal(evidence.maxActiveRenders, 1);
  const allPrepIds = new Set(evidence.events.filter((e) => e.event === "render-start").map((e) => e.requestId));
  const data = evidence.events.filter((e) => e.phase === "calls-data-read");
  assert(data.length > 0 && data.every((e) => allPrepIds.has(e.requestId)));
  const servedIds = new Set(evidence.events.filter((e) => e.phase === "calls-visitor").map((e) => e.requestId));
  assert(evidence.requests.filter((r) => r.id).every((r) => servedIds.has(r.id)));
  check("Logs correlate guarded visitor responses to prepared digests and bound active renders at one");
} catch (error) {
  evidence.failure = error.stack;
  throw error;
} finally {
  await Promise.all(processes.map((child) => stop(child, "SIGKILL")));
  await db.end();
  api.closeAllConnections();
  api.close();
  evidence.finishedAt = new Date().toISOString();
  evidence.diagnostics = errors;
  await writeFile(process.env.CALLS_VERIFY_EVIDENCE ?? "calls620-verification.json", JSON.stringify(evidence, null, 2) + "\n");
}
