// web/lib/og/icon.tsx: the site icon (the favicon and the touch icon): the wordmark's square, --highlight (white), centred
// on a --canvas (black) square, half its side, no radius. Colours come from globals.css through readOgTokens, read
// exactly as the preview card reads them (CLAUDE.md §5: tokens only). Drawn with next/og; no binary file in the repo.
import { readFile } from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";
import { readOgTokens } from "@/lib/og/tokens";

export async function renderIcon(side: number): Promise<ImageResponse> {
  const t = readOgTokens(await readFile(path.join(process.cwd(), "app/globals.css"), "utf8"));
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: t.canvas }}>
        <div style={{ width: side / 2, height: side / 2, background: t.highlight }} />
      </div>
    ),
    { width: side, height: side },
  );
}
