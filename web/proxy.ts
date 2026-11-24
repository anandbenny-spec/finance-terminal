import { NextResponse, type NextRequest } from "next/server";
import { servePrepared } from "@/lib/calls-delivery/read";

export const config = {
  matcher: [
    "/earnings",
    "/earnings.rsc",
    "/earnings.segments/:path*",
    "/companies/:slug/calls/:path*",
    "/companies/:slug/calls.rsc",
    "/companies/:slug/calls.segments/:path*",
  ],
};

export function proxy(request: NextRequest) {
  // This flag is set only in the worker's authenticated, loopback renderer.
  if (process.env.CALLS_PRIVATE_RENDERER === "1") return NextResponse.next();
  return servePrepared(request);
}
