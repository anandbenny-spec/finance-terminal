// The favicon (1 Nov 2026, #228): the white wordmark square on black. /favicon.ico is rewritten to this route
// (next.config.ts), so crawlers and link unfurlers that ask for it directly get the PNG instead of a 404.
import { renderIcon } from "@/lib/og/icon";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default async function Icon() {
  return renderIcon(size.width);
}
