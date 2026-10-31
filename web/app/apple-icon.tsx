// The touch icon (1 Nov 2026, #228): the same square, drawn at 180 px.
import { renderIcon } from "@/lib/og/icon";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default async function AppleIcon() {
  return renderIcon(size.width);
}
