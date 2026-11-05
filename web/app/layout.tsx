import type { Metadata, Viewport } from "next";

import { titleFont, uiFont } from "@/lib/fonts";

import "./globals.css";

const DESCRIPTION =
  "One sentence in. A finished short film out: script, voices, pictures, music and cuts. Then change it by saying what you want.";

// Link previews (chat apps, email) need absolute URLs. The deployment's own
// address is baked in at build time; SITE_URL overrides it.
const SITE_URL = process.env.SITE_URL || "https://139-185-59-132.sslip.io";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Dastango: a short film from one sentence", template: "%s · Dastango" },
  description: DESCRIPTION,
  applicationName: "Dastango",
  openGraph: {
    type: "website",
    siteName: "Dastango",
    title: "Dastango: a short film from one sentence",
    description: DESCRIPTION,
  },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0c0d0f" },
    { media: "(prefers-color-scheme: light)", color: "#ece1d3" },
  ],
  colorScheme: "dark light",
};

// Runs before the first paint, so a creator who chose a theme never sees the
// other one flash. No choice stored: the system setting decides (globals.css).
const THEME_BOOT = `try{var t=localStorage.getItem("dastango-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${titleFont.variable} ${uiFont.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
