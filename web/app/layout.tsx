import type { Metadata } from "next";
import { AppShell } from "@/components/shell/AppShell";
import { plexMono, plexSans } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "Finance Terminal",
  description: "An AI research terminal for Indian and US markets. Informational only, not investment advice.",
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
