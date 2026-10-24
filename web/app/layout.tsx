import type { Metadata } from "next";
import { AppShell } from "@/components/shell/AppShell";
import { SITE_DESCRIPTION, SITE_NAME, siteOrigin } from "@/lib/site";
import { plexMono, plexSans } from "./fonts";
import "./globals.css";

// The preview card (#86; Brief §10): title, description and image (app/opengraph-image.tsx and twitter-image.tsx, which
// Next adds to the head). `metadataBase` makes the image URL absolute from Vercel's environment (lib/site.ts).
export const metadata: Metadata = {
  metadataBase: siteOrigin(),
  title: SITE_NAME,
  description: SITE_DESCRIPTION,
  openGraph: { type: "website", siteName: SITE_NAME, title: SITE_NAME, description: SITE_DESCRIPTION },
  twitter: { card: "summary_large_image", title: SITE_NAME, description: SITE_DESCRIPTION },
};

// Fonts: Design 01 §2.7 (fonts.ts). `dark`: the site is dark only, and globals.css keeps shadcn's class-based
// `dark:` variant so its primitives look the same whatever the viewer's OS setting (see globals.css, part 1).
// Every page renders inside the shell (C2: Design 01 §4.1, §5.1).
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable} dark h-full`}>
      <body className="min-h-full">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
