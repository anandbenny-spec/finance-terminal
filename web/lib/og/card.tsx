// web/lib/og/card.tsx: the link's preview card (Open Graph and Twitter), drawn with next/og from the design tokens.
// It carries no figures (CLAUDE.md §1: no number the database didn't return): the name, what the site is, the
// module on this link, and the disclaimer. Satori draws with its bundled font; Plex isn't loaded for one image.
import { readFile } from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";
import { SITE_CARD_SUBTITLE, SITE_NAME, SITE_TAGLINE } from "@/lib/site";
import { readOgTokens } from "@/lib/og/tokens";

export const CARD_SIZE = { width: 1200, height: 630 } as const;
export const CARD_ALT = `${SITE_NAME}: ${SITE_CARD_SUBTITLE}. Informational only, not investment advice.`;
export const CARD_TYPE = "image/png";

export async function renderCard(): Promise<ImageResponse> {
  const t = readOgTokens(await readFile(path.join(process.cwd(), "app/globals.css"), "utf8"));
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", background: t.canvas, color: t.ink1, padding: 72 }}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <div style={{ width: 28, height: 28, background: t.amber, marginRight: 20 }} />
          <div style={{ fontSize: 40, fontWeight: 500, color: t.ink1 }}>{SITE_NAME}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 72, fontWeight: 600, lineHeight: 1.1, color: t.ink1 }}>{SITE_CARD_SUBTITLE}</div>
          <div style={{ fontSize: 34, lineHeight: 1.4, marginTop: 28, color: t.ink2, maxWidth: 940 }}>
            {SITE_TAGLINE}
          </div>
          <div style={{ display: "flex", marginTop: 36 }}>
            <div style={{ width: 160, height: 6, background: t.series1, marginRight: 8 }} />
            <div style={{ width: 160, height: 6, background: t.series2 }} />
          </div>
        </div>
        <div style={{ fontSize: 26, color: t.ink3 }}>Informational only, not investment advice.</div>
      </div>
    ),
    CARD_SIZE,
  );
}
