// The two faces, self-hosted from the files the kit supplied (Fontshare, ITF FFL:
// self-hosting and commercial use are allowed; the files must not be modified,
// subset or converted, so they are the supplied .woff2 exactly, licence text
// beside them in app/fonts/). Nothing is fetched from a font service at runtime.
//
// Chillax Semibold: every heading, large or small.  Synonym Regular: all other
// text. Each comes in one weight, so `font-synthesis: none` (globals.css) keeps
// the browser from faking a bold or an italic the kit does not have.
import localFont from "next/font/local";

export const titleFont = localFont({
  src: "../app/fonts/Chillax-Semibold.woff2",
  weight: "600",
  style: "normal",
  variable: "--font-title",
  display: "swap",
  fallback: ["system-ui", "sans-serif"],
});

export const uiFont = localFont({
  src: "../app/fonts/Synonym-Regular.woff2",
  weight: "400",
  style: "normal",
  variable: "--font-ui",
  display: "swap",
  fallback: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
});
