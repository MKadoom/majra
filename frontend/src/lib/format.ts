/** Metric formatting. Money keeps two decimals; counts get thin separators;
 *  ratios read as ratios. Everything renders in a tabular font (see .num). */

const MONEY = new Set(["spend", "cpc", "cpm", "cpa", "conversion_value", "revenue"]);
const RATIO = new Set(["ctr", "roas"]);

export function formatMeasure(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  if (key === "ctr") return `${n.toFixed(2)}%`;
  if (RATIO.has(key)) return n.toFixed(2);
  if (MONEY.has(key)) {
    return n.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  return Math.round(n).toLocaleString();
}

export const MEASURE_LABEL: Record<string, string> = {
  spend: "Spend",
  impressions: "Impressions",
  clicks: "Clicks",
  ctr: "CTR",
  cpc: "CPC",
  cpm: "CPM",
  cpa: "CPA",
  conversions: "Conversions",
  conversion_value: "Value",
  roas: "ROAS",
  sessions: "Sessions",
  users: "Users",
  revenue: "Revenue",
  reach: "Reach",
  engagement: "Engagement",
  followers: "Followers",
};

export const label = (k: string) =>
  MEASURE_LABEL[k] ?? k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ------------------------------------------------------------------- dates
export const isoDate = (d: Date) => d.toISOString().slice(0, 10);

export function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return isoDate(d);
}

/** Inclusive list of ISO dates from `from` to `to`. */
export function dateRange(from: string, to: string): string[] {
  const out: string[] = [];
  const cur = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (cur <= end) {
    out.push(isoDate(cur));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Download the current view as CSV — no server round-trip needed. */
export function downloadCsv(rows: Record<string, unknown>[], filename: string) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [
    cols.join(","),
    ...rows.map((r) => cols.map((c) => esc(r[c])).join(",")),
  ].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
