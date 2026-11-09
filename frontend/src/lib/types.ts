export type PlatformInfo = { platform: string; auth_kind: "oauth" | "service_account" };

/** A link a client opens to attach their own ad account to this instance. */
export type InviteLink = {
  url: string;
  platform: string;
  expires_in_days: number;
};

export type Connection = {
  id: number;
  platform: string;
  name: string;
  status: string;
  /** Account external_ids this connection syncs; null/empty = all of them. */
  synced_account_ids: string[] | null;
  created_at: string;
};

export type CardCharge = {
  id: number;
  date: string;
  amount: number;
  currency: string;
  note: string | null;
};

export type FundingMonth = {
  month: string; // YYYY-MM
  spend: number;
  charged: number;
  net: number;
  by_platform: Record<string, number>;
};

export type Funding = {
  currency: string;
  total_charged: number;
  total_spent: number;
  balance: number;
  range_from: string;
  range_to: string;
  range_spent: number;
  range_charged: number;
  daily: { date: string; spend: number; charged: number }[];
  monthly: FundingMonth[];
};

export type SavedView = {
  id: number;
  name: string;
  config: {
    dataset?: string;
    platforms?: string[];
    group_by?: string[];
    from?: string;
    to?: string;
    currency?: string;
  };
  created_at: string;
};

export type Freshness = {
  connection_id: number;
  platform: string;
  name: string;
  last_date: string | null;
  days_stale: number | null;
  stale: boolean;
};

export type FxRate = {
  currency: string;
  rate_to_usd: number;
  updated_at: string | null;
};

export type Budget = {
  id: number;
  name: string;
  platform: string | null;
  account_id: number | null;
  amount_monthly: number;
  currency: string;
  enabled: boolean;
  created_at: string;
};

export type Pacing = {
  budget_id: number;
  name: string;
  platform: string | null;
  account_id: number | null;
  currency: string;
  amount_monthly: number;
  spent_mtd: number;
  projected_month_end: number;
  pct_used: number | null;
  status: "over" | "under" | "on_track";
};

export type Account = {
  id: number;
  connection_id: number;
  platform: string;
  external_id: string;
  name: string;
  currency: string | null;
  timezone: string | null;
};

export type SyncJob = {
  id: number;
  connection_id: number;
  kind: string;
  date_from: string;
  date_to: string;
  breakdown: string;
  status: "pending" | "running" | "success" | "failed";
  stats: {
    accounts?: number;
    rows_fetched?: number;
    rows_deleted?: number;
    rows_inserted?: number;
  } | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type SyncLog = {
  ts: string;
  level: string;
  event: string;
  data: Record<string, unknown>;
};

export type Schedule = {
  id: number;
  connection_id: number;
  cadence: string;
  hour_utc: number;
  breakdowns: string[];
  restatement_days: number;
  backfill_days: number;
  enabled: boolean;
  last_enqueued_at: string | null;
};

/** A /metrics, /web-metrics or /social-metrics row: group keys plus measures. */
export type MetricRow = Record<string, string | number | null>;

export type MetricPage = {
  data: MetricRow[];
  page: number;
  page_size: number;
  total: number;
  currency?: string | null;
  warnings?: string[];
  /** Set when the figures sum more than one platform's purchase claims (SPEC §5). */
  purchase_overlap?: PurchaseOverlap | null;
};

/** Each platform claims the same donation, so a cross-platform sum overcounts. */
export type PurchaseOverlap = {
  platforms: string[];
  metrics: string[];
  note: string;
};

export type GtmResource = {
  kind: string;
  external_id: string | null;
  name: string | null;
  type: string | null;
  container_external_id: string | null;
  workspace_external_id: string | null;
  data: Record<string, unknown>;
};

export type GtmSnapshot = { captured_at: string | null; data: GtmResource[] };

export type ApiKey = {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type SavedReport = {
  id: number;
  name: string;
  dataset: "ads" | "web" | "social";
  params: Record<string, unknown>;
  destination: { kind: string; url?: string; to?: string; spreadsheet_id?: string };
  cadence: string;
  hour_utc: number;
  enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
};

export type DonationImport = {
  mapping: Record<string, string>;
  unmapped_columns: string[];
  rows_read: number;
  valid: number;
  paid: number;
  inserted: number;
  updated: number;
  skipped: { row: number; reason: string }[];
  skipped_count: number;
  statuses: Record<string, number>;
  by_platform: Record<string, { orders: number; amount: number }>;
  date_range: [string, string] | null;
  dry_run: boolean;
};

export type DonationCompareRow = {
  platform: string;
  claimed_purchases: number;
  claimed_value: number;
  spend: number;
  jood_orders: number;
  jood_amount: number;
  claim_ratio: number | null;
  cost_per_jood_donation: number | null;
  jood_roas: number | null;
  ga4_purchases: number;
  ga4_revenue: number;
  ga4_ratio: number | null;
};

export type DonationCompare = {
  date_from: string;
  date_to: string;
  currency: string;
  rows: DonationCompareRow[];
  totals: {
    jood_orders: number;
    jood_amount: number;
    jood_unattributed_orders: number;
    claimed_purchases_all_platforms: number;
    overclaim: number | null;
    ga4_purchases: number;
    ga4_revenue: number;
    overclaim_vs_ga4: number | null;
  };
  warnings: string[];
};
