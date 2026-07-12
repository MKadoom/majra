import type { Config } from "tailwindcss";

// Design tokens. The chrome is deliberately quiet — deep ink, hairline rules —
// because colour belongs to the DATA here: every platform is a "tributary" with
// its own hue, used consistently in charts, badges and legends.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "#0F1419", // deep ink — water at depth, not pure black
        surface: "#161C23", // raised panel
        raised: "#1C242D", // hover / active panel
        line: "#232C36", // hairline rule
        ink: "#E6EDF3", // primary text
        muted: "#8B98A5", // secondary text
        accent: "#57C4D8", // the single accent: focus, active nav, the channel
        good: "#5FBF8F",
        warn: "#E0B341",
        bad: "#E5707E",
        // Tributaries — one hue per platform, used everywhere that platform appears.
        meta: "#4F86F7",
        google_ads: "#F5A623",
        tiktok: "#25E5D0",
        snapchat: "#FFE14D",
        ga4: "#B57BFF",
        meta_social: "#F76D8E",
        gtm: "#7DD87D",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        // Metrics are monospaced with tabular figures so columns align and digits
        // don't jitter between refreshes. This is an instrument panel.
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        eyebrow: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.08em" }],
      },
      borderRadius: { panel: "10px" },
    },
  },
  plugins: [],
};
export default config;
