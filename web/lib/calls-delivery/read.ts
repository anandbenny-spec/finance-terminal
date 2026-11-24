import { createHash, randomUUID } from "node:crypto";
import { gunzipSync } from "node:zlib";
import { Client } from "pg";
import { defaultPath, preparedRequest } from "./paths.mjs";

const headers = {
  "cache-control": "private, no-store, max-age=0",
  "cdn-cache-control": "no-store",
  "vercel-cdn-cache-control": "no-store",
  vary: "RSC, Next-Router-State-Tree, Next-Router-Prefetch, Next-Router-Segment-Prefetch",
};

interface Artifact {
  known: boolean;
  company_exists: boolean | null;
  revision: string | null;
  shared_revision: string;
  render_token: string;
  completed_at: Date;
  path_exists: boolean;
  digest: string | null;
  body: Buffer | null;
}

export async function servePrepared(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const target = preparedRequest(url.pathname, request.headers);
  if (!target) return new Response(null, { status: 404, headers });
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(null, { status: 405, headers: { ...headers, allow: "GET, HEAD" } });
  }

  const build = process.env.CALLS_BUILD_ID;
  const connectionString = process.env.CALLS_READER_DATABASE_URL;
  const requestId = request.headers.get("x-vercel-id") ?? randomUUID();
  const context = { phase: "calls-visitor", requestId, build, path: target.path, variant: target.variant };

  try {
    if (!build || !connectionString) throw new Error("not configured");
    const db = new Client({
      connectionString,
      application_name: "calls-artifact-reader",
      connectionTimeoutMillis: 4_000,
      statement_timeout: 4_000,
      options: "-c default_transaction_read_only=on",
    });
    // A server-side disconnect between query completion and end is harmless.
    db.on("error", () => {});
    let result: Artifact;
    try {
      await db.connect();
      const { rows } = await db.query<Artifact>(`
        select exists (select 1 from calls_delivery.revision where scope = $2) as known,
               m.company_exists, m.revision, m.shared_revision, m.render_token, m.completed_at,
               exists (
                 select 1 from calls_delivery.current_response
                 where build_id = $1 and scope = $2 and path = $3 and variant = 'html'
               ) as path_exists, r.digest, r.body
        from (values (1)) as request(id)
        left join calls_delivery.current_manifest m on m.build_id = $1 and m.scope = $2
        left join calls_delivery.current_response r
          on r.build_id = $1 and r.scope = $2 and r.path = $3 and r.variant = $4
      `, [build, target.scope, target.path, target.variant]);
      result = rows[0];
    } finally {
      await db.end();
    }

    if (!result.known || result.company_exists === false) {
      return new Response(null, { status: 404, headers });
    }
    if (result.revision === null) throw new Error("pending revision");
    if (target.quarter && !result.path_exists) {
      return new Response(null, { status: 307, headers: { ...headers, location: new URL(defaultPath(target.scope), url).href } });
    }
    if (!result.body || !result.digest) {
      if (target.variant.startsWith("segment:") && result.path_exists) {
        return new Response(null, { status: 404, headers });
      }
      throw new Error("missing artifact");
    }
    const body = gunzipSync(result.body);
    if (createHash("sha256").update(body).digest("hex") !== result.digest) {
      throw new Error("artifact integrity mismatch");
    }
    const revision = `${result.revision}.${result.shared_revision}`;
    const renderId = `${build}/${result.render_token}`;
    console.info(JSON.stringify({ ...context, revision, renderId, digest: result.digest }));
    return new Response(request.method === "HEAD" ? null : new Uint8Array(body), {
      headers: {
        ...headers,
        "content-type": target.variant === "html" ? "text/html; charset=utf-8" : "text/x-component",
        "content-length": String(body.length),
        "x-calls-request-id": requestId,
        "x-calls-render-id": renderId,
        "x-calls-revision": revision,
        "x-calls-sha256": result.digest,
        "x-calls-completed-at": result.completed_at.toISOString(),
        "x-nextjs-stale-time": "0",
      },
    });
  } catch (error) {
    console.warn(JSON.stringify({ ...context, status: 503, reason: error instanceof Error && ["not configured", "pending revision", "missing artifact", "artifact integrity mismatch"].includes(error.message) ? error.message : "read failed" }));
    return new Response("Prepared page temporarily unavailable", {
      status: 503,
      headers: { ...headers, "retry-after": "5", "x-calls-request-id": requestId },
    });
  }
}
