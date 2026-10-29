// web/lib/site.ts: what the site says about itself in a link's preview card (Brief §10, the stage 1 launch
// checklist; #86), and the origin those absolute URLs hang off.

export const SITE_NAME = "Finance Terminal";

/** The one-line positioning (Brief §12, decided by Anand on 28 Oct 2026, 08:49 IST). */
export const SITE_TAGLINE = "An AI research terminal for Indian markets and the Fed–RBI policy cycle.";
export const SITE_DESCRIPTION = `${SITE_TAGLINE} Informational only, not investment advice.`;

/** The line under the name on the preview image: what this link shows today (module 1). */
export const SITE_CARD_SUBTITLE = "Fed vs RBI policy tracker";

type Env = Record<string, string | undefined>;

/**
 * The origin that absolute URLs in the page head (the preview image) are built from. Read from Vercel's system
 * environment variables, never typed: the production domain on a production build, the deployment's own host on a
 * preview, and localhost when neither is set (local `next dev`). Vercel's values carry no scheme.
 */
export function siteOrigin(env: Env = process.env): URL {
  const host =
    env.VERCEL_ENV === "production" ? env.VERCEL_PROJECT_PRODUCTION_URL || env.VERCEL_URL : env.VERCEL_URL || env.VERCEL_PROJECT_PRODUCTION_URL;
  if (host) return new URL(`https://${host.replace(/^https?:\/\//, "").replace(/\/+$/, "")}`);
  return new URL(`http://localhost:${env.PORT || "3000"}`);
}
