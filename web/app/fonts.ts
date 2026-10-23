// Design 01 §1.3. latin-ext is required: the ₹ sign lives in that subset (checked on the font files).
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";

export const plexSans = IBM_Plex_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

export const plexMono = IBM_Plex_Mono({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});
// app/layout.tsx: <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
