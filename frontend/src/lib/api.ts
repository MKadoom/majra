"use client";

/** Every browser call goes through the server-side proxy — no tokens in JS. */
const BASE = "/api/proxy";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((b) => b?.detail ?? b?.error)
      .catch(() => null);
    throw new ApiError(
      res.status,
      typeof detail === "string" ? detail : `Request failed (${res.status})`,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};

/** Build a query string, repeating keys for list params (platform, group_by…). */
export function qs(
  params: Record<string, string | number | boolean | string[] | undefined>,
) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => sp.append(k, String(item)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------- vocabulary
/** One hue per platform — the "tributary" colour, used everywhere it appears. */
export const PLATFORM_COLOR: Record<string, string> = {
  meta: "#4F86F7",
  google_ads: "#F5A623",
  tiktok: "#25E5D0",
  snapchat: "#FFE14D",
  ga4: "#B57BFF",
  meta_social: "#F76D8E",
  gtm: "#7DD87D",
};

export const PLATFORM_LABEL: Record<string, string> = {
  meta: "Meta Ads",
  google_ads: "Google Ads",
  tiktok: "TikTok Ads",
  snapchat: "Snapchat Ads",
  ga4: "Google Analytics 4",
  meta_social: "Facebook & Instagram",
  gtm: "Tag Manager",
};

export const platformColor = (p: string) => PLATFORM_COLOR[p] ?? "#8B98A5";
export const platformLabel = (p: string) => PLATFORM_LABEL[p] ?? p;

/** Which platforms feed each dataset — mirrors the connectors' `dataset`. */
export const AD_PLATFORMS = ["meta", "google_ads", "tiktok", "snapchat"];

export type Dataset = "ads" | "web" | "social" | "tags";

export const DATASET_ENDPOINT: Record<Exclude<Dataset, "tags">, string> = {
  ads: "/metrics",
  web: "/web-metrics",
  social: "/social-metrics",
};

/** Measures each dataset returns, in display order. */
export const DATASET_MEASURES: Record<Exclude<Dataset, "tags">, string[]> = {
  ads: ["spend", "impressions", "clicks", "ctr", "cpc", "conversions", "conversion_value", "roas"],
  web: ["sessions", "users", "conversions", "revenue"],
  social: ["impressions", "reach", "engagement", "followers"],
};

export const DATASET_GROUPS: Record<Exclude<Dataset, "tags">, string[]> = {
  ads: ["platform", "date", "account", "campaign", "adset", "ad", "country", "device"],
  web: ["date", "source", "medium", "campaign", "account"],
  social: ["date", "grain", "external_ref", "account"],
};
