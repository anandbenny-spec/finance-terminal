// Observe actual server-side fetches, including requests outside the normal
// Supabase client. Never log query strings, credentials or response bodies.
import { AsyncLocalStorage } from "node:async_hooks";
import { channel } from "node:diagnostics_channel";

interface ReadContext {
  requestId: string;
  phase: "visitor" | "preparation";
}

const state = globalThis as typeof globalThis & {
  callsReadContext?: AsyncLocalStorage<ReadContext>;
};

export function installReadTrace() {
  if (state.callsReadContext) return state.callsReadContext;
  const context = new AsyncLocalStorage<ReadContext>();
  state.callsReadContext = context;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (url && process.env.CALLS_BUILD_ID) {
    const origin = new URL(url).origin;
    channel("undici:request:create").subscribe((message) => {
      const { request } = message as { request: { origin: string; path: string } };
      if (request.origin !== origin || !request.path.startsWith("/rest/v1/")) return;
      const scope = context.getStore();
      console.info(JSON.stringify({
        phase: "calls-api-outbound", build: process.env.CALLS_BUILD_ID,
        origin: scope?.phase ?? "unattributed",
        requestId: scope?.requestId ?? null, view: request.path.split("?")[0],
      }));
    });
  }
  return context;
}
