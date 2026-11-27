import { createHash, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { once } from "node:events";
import { gzipSync } from "node:zlib";
import { defaultPath } from "../../lib/calls-delivery/paths.mjs";
import { installReadTrace } from "../../lib/calls-delivery/trace.ts";
import { listStatusText } from "../../lib/equity/earnings-status.ts";
import Capture from "./capture.cjs";

const require = createRequire(import.meta.url);
const { parse } = require("next/dist/compiled/node-html-parser");
const failedRead = /couldn(?:['’]|&#(?:x0*27|0*39);|&apos;|\\u0027)t load/i;
const callSlug = /^[a-z0-9-]{1,40}$/;

export async function startRenderer(buildId) {
  if ((await readFile(".next/BUILD_ID", "utf8")).trim() !== buildId) {
    throw Error("worker must use the same Next build as the visitor deployment");
  }
  const { preview } = JSON.parse(await readFile(".next/prerender-manifest.json", "utf8"));
  const secret = randomUUID();
  process.env.CALLS_PRIVATE_RENDERER = "1";
  const next = require("next");
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  const app = next({ dir: process.cwd(), dev: false, hostname: "127.0.0.1", port, httpServer: server });
  await app.prepare();
  const handle = app.getRequestHandler();
  server.on("request", (req, res) => {
    if (req.method !== "GET" || req.headers["x-calls-prepare"] !== secret) {
      res.writeHead(404).end();
      return;
    }
    const requestId = req.headers["x-calls-render-id"];
    installReadTrace().run({ requestId, phase: "preparation" }, () => handle(req, res)).catch(() => res.destroy());
  });
  return {
    async render(path, expected, requestId) {
      Capture.entry = undefined;
      const response = await fetch(`http://127.0.0.1:${port}${path}`, {
        headers: { "x-calls-prepare": secret, "x-calls-render-id": requestId, "x-prerender-revalidate": preview.previewModeId },
        redirect: "error",
      });
      const body = Buffer.from(await response.arrayBuffer());
      const captured = Capture.entry;
      Capture.entry = undefined;
      if (response.status !== 200 || !(captured?.key === path || captured?.key.endsWith(`/$${path}`)) || captured.value.postponed ||
          (captured.value.status !== undefined && captured.value.status !== 200) || !body.equals(Buffer.from(captured.value.html))) {
        throw Error(`incomplete Next response for ${path} (${response.status}, capture: ${Boolean(captured)}, postponed: ${Boolean(captured?.value.postponed)})`);
      }

      if (captured.cacheControl?.revalidate === 5) throw Error(`failed read in ${path}`);
      const { rscData, segmentData } = captured.value;
      if (!rscData?.length || !segmentData?.get("/_tree") || !segmentData.get("/_index")) {
        throw Error(`missing Next navigation data for ${path}`);
      }
      const parts = [["html", body], ["rsc", rscData], ...Array.from(segmentData, ([key, bytes]) => [`segment:${key}`, bytes])];
      const doc = parse(body.toString());
      if (failedRead.test(doc.text) || parts.some(([, bytes]) => !bytes.length || failedRead.test(bytes.toString()))) {
        throw Error(`failed panel in ${path}`);
      }
      validatePage(doc, path, expected);
      return parts.map(([variant, bytes]) => ({
        path, variant, selectedCall: expected.selected?.call_slug ?? null,
        digest: createHash("sha256").update(bytes).digest("hex"), body: gzipSync(bytes),
      }));
    },
    async close() {
      await app.close();
      server.close();
    },
  };
}

export function validateEarningsList(doc, list) {
  const panel = doc.querySelector('section[aria-labelledby="panel-earnings"]');
  const rows = panel?.querySelectorAll("tbody tr") ?? [];
  const expected = new Map(list.map((item) => [defaultPath(item.company_slug), item]));
  if (!panel || rows.length !== list.length || (!list.length && !panel.text.includes("No companies are listed yet."))) {
    throw Error("incomplete earnings list");
  }
  for (const row of rows) {
    const cells = row.querySelectorAll("td");
    const href = cells[0]?.querySelector("a")?.getAttribute("href");
    const item = expected.get(href);
    if (!item || cells[1]?.querySelector("span")?.text.trim() !== listStatusText(item) ||
        cells[2]?.text.trim() !== (item.fiscal_label ?? "Not available")) {
      throw Error("incorrect earnings list row");
    }
    expected.delete(href);
  }
}

function validatePage(doc, path, { company, calls, selected, list }) {
  if (list) return validateEarningsList(doc, list);

  const base = defaultPath(company.slug);
  const headings = doc.querySelectorAll("h1").map((element) => element.text.trim());
  const us = company.market === "US";
  if (![company.name, company.short_name].some((name) => headings.includes(name)) ||
      (!us && !doc.querySelector("#panel-calls"))) {
    throw Error(`wrong company or missing Calls panel in ${path}`);
  }
  if (us) {
    if (!doc.text.includes("US earnings calls aren't covered")) throw Error(`incorrect market in ${path}`);
    return;
  }

  const active = doc.querySelectorAll("a[aria-current=true]");
  if (!selected) {
    if (active.length || !doc.text.includes("No transcript found")) throw Error(`invalid empty state in ${path}`);
    return;
  }
  if (active.length !== 1 || active[0].getAttribute("href") !== `${base}/${selected.call_slug}` ||
      doc.querySelector("#panel-call-summary")?.text !== `${selected.fiscal_label} call: what was said`) {
    throw Error(`wrong selected call in ${path}`);
  }
  const table = doc.querySelector('section[aria-labelledby="panel-calls"]');
  const hrefs = [...new Set((table?.querySelectorAll("table a") ?? []).map((a) => a.getAttribute("href"))
    .filter((href) => href?.startsWith(`${base}/`)))];
  if (hrefs.join() !== calls.map((call) => `${base}/${call.call_slug}`).join()) {
    throw Error(`incorrect quarter links in ${path}`);
  }
}

export function anonymousApi() {
  const { NEXT_PUBLIC_SUPABASE_URL: url, NEXT_PUBLIC_SUPABASE_ANON_KEY: key } = process.env;
  if (!url || !key) throw Error("missing anonymous Data API configuration");
  return async (view, query) => {
    const result = [];
    let count;
    do {
      const target = new URL(`/rest/v1/${view}`, url);
      target.search = new URLSearchParams({ ...query, offset: String(result.length), limit: "1000" });
      const response = await fetch(target, {
        headers: {
          apikey: key, authorization: `Bearer ${key}`, "accept-profile": "api", prefer: "count=exact",
        },
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) throw Error(`oracle ${view} HTTP ${response.status}`);
      const total = response.headers.get("content-range")?.split("/").at(-1);
      if (!total || !/^\d+$/.test(total) || (count !== undefined && count !== Number(total))) {
        throw Error(`unstable oracle count for ${view}`);
      }
      count = Number(total);
      const batch = await response.json();
      if (!Array.isArray(batch) || (!batch.length && result.length < count)) throw Error(`incomplete oracle ${view}`);
      result.push(...batch);
    } while (result.length < count);
    if (result.length !== count) throw Error(`incorrect oracle count for ${view}`);
    return result;
  };
}

export async function planScope(read, scope) {
  if (scope === "@earnings") {
    const [companies, latest] = await Promise.all([
      read("companies", { select: "slug", market: "eq.IN", order: "slug" }),
      read("earnings_latest", { select: "company_slug,call_slug,fiscal_label,status,summary_state,roles_partial", order: "company_slug" }),
    ]);
    const india = new Set(companies.map((c) => c.slug));
    const list = latest.filter((row) => india.has(row.company_slug));
    if (new Set(list.map((r) => r.company_slug)).size !== india.size || india.size !== list.length) {
      throw Error("incomplete earnings list oracle");
    }
    return { exists: true, discovery: false, routes: [[defaultPath(scope), { list }]] };
  }
  const companies = await read("companies", { select: "slug,name,short_name,market", slug: `eq.${scope}` });
  if (companies.length > 1) throw Error(`ambiguous company ${scope}`);
  const [company] = companies;
  if (!company) return { exists: false, discovery: false, routes: [] };
  const calls = company.market === "IN" ? await read("earnings_calls", {
    select: "call_slug,fiscal_label,is_default",
    company_slug: `eq.${scope}`,
    order: "period_end.desc.nullslast,filed_at.desc,call_slug",
  }) : [];
  const defaults = calls.filter((call) => call.is_default);
  if (defaults.length !== Math.min(1, calls.length) ||
      new Set(calls.map((c) => c.call_slug)).size !== calls.length ||
      calls.some((c) => !callSlug.test(c.call_slug))) {
    throw Error(`ambiguous public call selection for ${scope}`);
  }
  const path = defaultPath(scope);
  return {
    exists: true,
    discovery: company.market === "IN" && !calls.length,
    routes: [
      [path, { company, calls, selected: defaults[0] }],
      ...calls.map((selected) => [`${path}/${selected.call_slug}`, { company, calls, selected }]),
    ],
  };
}
