import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// Design tokens. majra's chrome used to be deliberately quiet; this is the bolder
// refresh — deeper, cooler ink, a punchier cyan "channel", crisper hairlines and
// real elevation — while keeping the rule that COLOUR BELONGS TO THE DATA: every
// platform is a tributary with its own hue, used consistently in charts and badges.
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "#0A0D12", // app background — near-black with a cold blue cast
        surface: "#12161D", // raised panel / card
        raised: "#1B212B", // hover / active / elevated panel
        line: "#252E3A", // hairline rule (a touch more present now)
        ink: "#EDF2F8", // primary text — brighter for punchier contrast
        muted: "#94A2B2", // secondary text
        accent: "#22D3EE", // the single accent: focus, active nav, the channel
        "accent-strong": "#0EA5C4", // deeper cyan for gradients / pressed
        good: "#34D399",
        warn: "#FBBF24",
        bad: "#F87171",
        // shadcn semantic aliases → majra tokens, so shadcn components inherit the
        // theme without a parallel variable layer.
        border: "#252E3A",
        input: "#252E3A",
        ring: "#22D3EE",
        background: "#0A0D12",
        foreground: "#EDF2F8",
        // Tributaries — one hue per platform, used everywhere that platform appears.
        meta: "#4F86F7",
        google_ads: "#F5A623",
        tiktok: "#25E5D0",
        snapchat: "#FFE14D",
        whatsapp: "#25D366",
        ga4: "#B57BFF",
        meta_social: "#F76D8E",
        gtm: "#7DD87D",
      },
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Noto Sans Arabic",
          "Geeza Pro",
          "Tahoma",
          "Roboto",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        eyebrow: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.08em" }],
      },
      borderRadius: {
        panel: "14px",
        lg: "12px",
        md: "10px",
        sm: "8px",
      },
      boxShadow: {
        // Soft, cool elevation for cards; a stronger one for popovers/dialogs.
        card: "0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -12px rgba(0,0,0,0.6)",
        pop: "0 8px 40px -8px rgba(0,0,0,0.7)",
        // A faint cyan glow for primary actions / focus.
        glow: "0 0 0 1px rgba(34,211,238,0.25), 0 6px 20px -6px rgba(34,211,238,0.35)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out both",
      },
    },
  },
  plugins: [animate],
};
export default config;
