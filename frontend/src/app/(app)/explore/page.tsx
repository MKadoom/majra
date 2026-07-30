"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Empty, ErrorNote, Panel, PlatformDot, Spinner, Status } from "@/components/ui";
import {
  AD_PLATFORMS,
  DATASET_ENDPOINT,
  DATASET_GROUPS,
  DATASET_MEASURES,
  Dataset,
  PRIMARY_MEASURES,
  api,
  currencyOptions,
  exportUrl,
  platformColor,
  qs,
} from "@/lib/api";
import { daysAgo, isoDate, pctDelta, prevWindow } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type { GtmSnapshot, MetricPage, MetricRow, SavedView } from "@/lib/types";

const DATASETS: Dataset[] = ["ads", "web", "social", "tags"];

/**
 * A small up/down badge showing change vs the previous equal-length period.
 * `null` (no baseline — e.g. a brand-new account with no prior window) renders
 * nothing rather than a misleading "0%". Up is good for most measures; callers
 * that invert that (a drop in CPA is good) can pass `invert`.
 */
function DeltaBadge({
  curr,
  prev,
  invert = false,
}: {
  curr: number | null | undefined;
  prev: number | null | undefined;
  invert?: boolean;
}) {
  const { t, measure } = useLocale();
  const pct = pctDelta(curr, prev);
  if (pct === null) return null;
  const up = pct > 0;
  const good = invert ? !up : up;
  const cls = good ? "text-good" : "text-bad";
  const label =
    pct === 0
      ? t("explore.deltaFlat")
      : up
        ? t("explore.deltaUp", { pct: Math.abs(pct).toFixed(0) })
        : t("explore.deltaDown", { pct: Math.abs(pct).toFixed(0) });
  return <span className={`ms-2 text-xs font-medium ${cls}`}>{label}</span>;
}

export default function ExplorePage() {
  const { t, m: mLabel, g: gLabel, p: pName, measure, dir } = useLocale();
  const [dataset, setDataset] = useState<Dataset>("ads");
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(daysAgo(1));
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<string[]>([]);

  const [totals, setTotals] = useState<MetricRow | null>(null);
  const [prevTotals, setPrevTotals] = useState<MetricRow | null>(null);
  const [series, setSeries] = useState<MetricRow[]>([]);
  const [table, setTable] = useState<MetricRow[]>([]);
  const [tags, setTags] = useState<GtmSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [compare, setCompare] = useState(false);
  const [currency, setCurrency] = useState(""); // "" = raw per-currency
  const [fxCurrencies, setFxCurrencies] = useState<string[]>([]);
  const [views, setViews] = useState<SavedView[]>([]);

  // Reporting-currency options: the curated shortlist, limited to those with a
  // configured rate (the FX table holds ~160, but the dropdown stays short).
  useEffect(() => {
    api
      .get<{ currency: string }[]>("/fx-rates")
      .then((rs) => setFxCurrencies(currencyOptions(rs.map((r) => r.currency))))
      .catch(() => setFxCurrencies(["USD"]));
  }, []);

  const loadViews = useCallback(() => {
    void api.get<SavedView[]>("/views").then(setViews).catch(() => setViews([]));
  }, []);
  useEffect(() => {
    loadViews();
  }, [loadViews]);

  async function saveView() {
    const name = window.prompt(t("explore.viewName"));
    if (!name) return;
    await api.post("/views", {
      name,
      config: { dataset, platforms, group_by: groupBy, from, to, currency },
    });
    loadViews();
  }

  function loadView(v: SavedView) {
    const c = v.config || {};
    if (c.dataset) setDataset(c.dataset as Dataset);
    setPlatforms(c.platforms ?? []);
    setGroupBy(c.group_by ?? []);
    if (c.from) setFrom(c.from);
    if (c.to) setTo(c.to);
    setCurrency(c.currency ?? "");
  }

  const measures = dataset === "tags" ? [] : DATASET_MEASURES[dataset];
  const groupOptions = dataset === "tags" ? [] : DATASET_GROUPS[dataset];
  const timeKey = dataset === "ads" ? "platform" : null;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (dataset === "tags") {
        setTags(await api.get<GtmSnapshot>("/gtm-resources"));
        return;
      }
      const endpoint = DATASET_ENDPOINT[dataset];
      const base = {
        date_from: from,
        date_to: to,
        ...(dataset === "ads" && platforms.length ? { platform: platforms } : {}),
        ...(dataset === "ads" && currency ? { currency } : {}),
      };

      // Three questions, three queries: the headline, the shape over time, and
      // the breakdown the person actually asked for.
      const [totalsRes, seriesRes, tableRes] = await Promise.all([
        api.get<MetricPage>(`${endpoint}${qs(base)}`),
        api.get<MetricPage>(
          `${endpoint}${qs({
            ...base,
            group_by: timeKey ? [timeKey, "date"] : ["date"],
            sort: "date",
            page_size: 1000,
          })}`,
        ),
        groupBy.length
          ? api.get<MetricPage>(
              `${endpoint}${qs({
                ...base,
                group_by: groupBy,
                sort: `-${measures[0]}`,
                page_size: 100,
              })}`,
            )
          : Promise.resolve<MetricPage>({ data: [], page: 1, page_size: 0, total: 0 }),
      ]);

      setTotals(totalsRes.data[0] ?? null);
      setSeries(seriesRes.data);
      setTable(tableRes.data);

      // Period-over-period: the immediately preceding window of equal length.
      // Previous spend means nothing without a baseline, so we fetch it only
      // when the person asks for it — it's a second (cheap) query, not free.
      if (compare) {
        const { prevFrom, prevTo } = prevWindow(from, to);
        const prevRes = await api
          .get<MetricPage>(
            `${endpoint}${qs({
              ...base,
              date_from: prevFrom,
              date_to: prevTo,
            })}`,
          )
          .catch(() => null);
        setPrevTotals(prevRes?.data[0] ?? null);
      } else {
        setPrevTotals(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("explore.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [dataset, from, to, platforms, groupBy, measures, timeKey, currency, compare, t]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Pivot the time series into one line per platform (or a single line). */
  const chart = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string>>();
    const keys = new Set<string>();
    const metric = measures[0];
    for (const row of series) {
      const date = String(row.date ?? "");
      const key = timeKey ? String(row[timeKey] ?? "all") : "all";
      keys.add(key);
      const point = byDate.get(date) ?? { date };
      point[key] = Number(row[metric] ?? 0);
      byDate.set(date, point);
    }
    return {
      points: [...byDate.values()].sort((a, b) =>
        String(a.date).localeCompare(String(b.date)),
      ),
      keys: [...keys],
      metric,
    };
  }, [series, measures, timeKey]);

  /**
   * Per-platform totals for the headline measure. The blended KPI cards answer
   * "how did we do"; this answers "which channel did it" — without making the
   * person filter to one platform at a time and hold numbers in their head.
   * Derived from the series we already fetched (grouped by platform × date),
   * so it costs no extra request.
   */
  const byPlatform = useMemo(() => {
    if (dataset !== "ads" || !timeKey) return [];
    const metric = measures[0];
    const sums = new Map<
      string,
      { value: number; impressions: number; rows: number; last: string }
    >();
    for (const row of series) {
      const key = String(row[timeKey] ?? "");
      if (!key) continue;
      const cur = sums.get(key) ?? { value: 0, impressions: 0, rows: 0, last: "" };
      cur.value += Number(row[metric] ?? 0);
      cur.impressions += Number(row.impressions ?? 0);
      cur.rows += 1;
      // ISO dates sort lexically, so the max string is the latest day we hold.
      const d = String(row.date ?? "");
      if (d > cur.last) cur.last = d;
      sums.set(key, cur);
    }
    const total = [...sums.values()].reduce((a, b) => a + b.value, 0);
    return [...sums.entries()]
      .map(([platform, { value, impressions, rows, last }]) => ({
        platform,
        value,
        impressions,
        rows,
        share: total > 0 ? value / total : 0,
        // A platform whose newest day is behind the window end hasn't caught
        // up. Saying so beats letting a short bar read as poor performance.
        staleThrough: last && last < to ? last : null,
      }))
      .sort((a, b) => b.value - a.value);
  }, [series, dataset, timeKey, measures, to]);

  const togglePlatform = (p: string) =>
    setPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );

  const toggleGroup = (g: string) =>
    setGroupBy((prev) => (prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]));

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{t("explore.title")}</h1>
          <p className="mt-0.5 text-sm text-muted">{t(`dataset.${dataset}.note`)}</p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label htmlFor="from" className="eyebrow mb-1 block">
              {t("common.from")}
            </label>
            <input
              id="from"
              type="date"
              className="field"
              value={from}
              max={to}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="to" className="eyebrow mb-1 block">
              {t("common.to")}
            </label>
            <input
              id="to"
              type="date"
              className="field"
              value={to}
              min={from}
              max={isoDate(new Date())}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          <label className="mb-0.5 flex cursor-pointer items-end gap-2 pb-1.5 text-sm text-muted">
            <input
              type="checkbox"
              className="h-4 w-4 accent-accent"
              checked={compare}
              onChange={(e) => setCompare(e.target.checked)}
            />
            {t("explore.compare")}
          </label>
          {dataset === "ads" && fxCurrencies.length > 0 && (
            <div>
              <label htmlFor="ccy" className="eyebrow mb-1 block">
                {t("explore.currency")}
              </label>
              <select
                id="ccy"
                className="field"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                <option value="">{t("explore.currencyRaw")}</option>
                {fxCurrencies.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="ms-auto flex items-end gap-2">
            {views.length > 0 && (
              <select
                className="field"
                value=""
                onChange={(e) => {
                  const v = views.find((x) => String(x.id) === e.target.value);
                  if (v) loadView(v);
                }}
                aria-label={t("explore.views")}
              >
                <option value="">{t("explore.views")}</option>
                {views.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
            <button className="btn-quiet" onClick={saveView}>
              {t("explore.saveView")}
            </button>
          </div>
        </div>
      </header>

      {/* Dataset switcher — the four shapes the pipeline produces. */}
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Dataset">
        {DATASETS.map((d) => (
          <button
            key={d}
            role="tab"
            aria-selected={dataset === d}
            onClick={() => {
              setDataset(d);
              setGroupBy([]);
            }}
            className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
              dataset === d
                ? "border-accent bg-accent/10 text-ink"
                : "border-line bg-surface text-muted hover:text-ink"
            }`}
          >
            {t(`dataset.${d}`)}
          </button>
        ))}
      </div>

      {error && <ErrorNote message={error} />}

      {dataset === "tags" ? (
        <TagsView snapshot={tags} loading={loading} />
      ) : (
        <>
          {dataset === "ads" && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">{t("explore.platforms")}</span>
              {AD_PLATFORMS.map((p) => {
                // Three distinct states, not two: "all" (no filter set) reads
                // neutral, an explicitly picked platform reads lit, and one
                // excluded by someone else's pick reads clearly switched off.
                const filtering = platforms.length > 0;
                const picked = platforms.includes(p);
                const state = !filtering ? "all" : picked ? "on" : "off";
                return (
                  <button
                    key={p}
                    onClick={() => togglePlatform(p)}
                    aria-pressed={picked}
                    title={
                      state === "off"
                        ? `${pName(p)} — ${t("common.showAll")}`
                        : pName(p)
                    }
                    className={`flex min-h-[32px] items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm transition-colors ${
                      state === "on"
                        ? "border-accent bg-accent/15 font-medium text-ink"
                        : state === "all"
                          ? "border-line bg-surface text-ink"
                          : // Muted, not faint: text-muted on surface is 5.8:1,
                          // where the /60 tint it replaces measured 2.9:1 and
                          // failed WCAG AA. The strike-through carries the
                          // "excluded" meaning; contrast stays legible.
                          "border-line bg-transparent text-muted line-through decoration-muted/60"
                    }`}
                  >
                    <span className={state === "off" ? "opacity-60" : ""}>
                      <PlatformDot platform={p} />
                    </span>
                    {pName(p)}
                  </button>
                );
              })}
              {platforms.length > 0 && (
                <button
                  onClick={() => setPlatforms([])}
                  className="text-sm text-muted hover:text-ink"
                >
                  {t("common.showAll")}
                </button>
              )}
            </div>
          )}

          {/* Headline numbers, in two tiers. The four you act on are large;
              the volume and efficiency numbers behind them stay available but
              quiet, so the eye lands on the decision first. */}
          {loading && !totals ? (
            <Spinner />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {measures.slice(0, PRIMARY_MEASURES).map((m) => (
                  <div key={m} className="panel p-4">
                    <p className="eyebrow flex items-center">
                      {mLabel(m)}
                      {compare && prevTotals && (
                        <DeltaBadge
                          curr={totals ? Number(totals[m]) : null}
                          prev={Number(prevTotals[m])}
                          invert={["cpa", "cpc", "cpm"].includes(m)}
                        />
                      )}
                    </p>
                    <p className="num mt-1.5 text-xl">
                      {totals ? measure(m, totals[m]) : "—"}
                    </p>
                  </div>
                ))}
              </div>

              {measures.length > PRIMARY_MEASURES && (
                <div className="panel grid grid-cols-2 gap-x-6 gap-y-3 p-4 sm:grid-cols-3 lg:grid-cols-5">
                  {measures.slice(PRIMARY_MEASURES).map((m) => (
                    <div
                      key={m}
                      className="flex items-baseline justify-between gap-2"
                    >
                      <span className="eyebrow flex items-center truncate">
                        {mLabel(m)}
                        {compare && prevTotals && (
                          <DeltaBadge
                            curr={totals ? Number(totals[m]) : null}
                            prev={Number(prevTotals[m])}
                            invert={["cpa", "cpc", "cpm"].includes(m)}
                          />
                        )}
                      </span>
                      <span className="num text-sm">
                        {totals ? measure(m, totals[m]) : "—"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Which channel produced the headline number. One row per platform,
              with a share bar in that platform's own hue. A platform that
              returned rows but no spend says so plainly, rather than silently
              contributing nothing to a blended total. */}
          {!loading && byPlatform.length > 0 && (
            <Panel title={t("explore.byPlatform", { metric: mLabel(chart.metric) })}>
              <ul className="divide-y divide-line">
                {byPlatform.map(({ platform, value, share, staleThrough }) => (
                  <li
                    key={platform}
                    className="flex items-center gap-3 px-4 py-2.5"
                  >
                    <span className="flex min-w-[7.5rem] items-center gap-2">
                      <PlatformDot platform={platform} />
                      <span className="truncate text-sm">{pName(platform)}</span>
                    </span>
                    {staleThrough && (
                      <span
                        className="whitespace-nowrap rounded border border-line px-1.5 py-0.5 text-eyebrow uppercase text-muted"
                        title={t("explore.staleTitle")}
                      >
                        {t("explore.through", { date: staleThrough })}
                      </span>
                    )}
                    <span
                      className="h-1.5 flex-1 overflow-hidden rounded-full bg-line"
                      role="presentation"
                    >
                      <span
                        className="block h-full rounded-full transition-[width]"
                        style={{
                          width: `${Math.max(share * 100, value > 0 ? 2 : 0)}%`,
                          backgroundColor: platformColor(platform),
                        }}
                      />
                    </span>
                    <span className="num w-24 text-end text-sm">
                      {measure(chart.metric, value)}
                    </span>
                    <span className="num w-14 text-end text-sm text-muted">
                      {value > 0 ? `${(share * 100).toFixed(0)}%` : "—"}
                    </span>
                  </li>
                ))}
              </ul>
              {byPlatform.some((p) => p.value === 0 && p.impressions === 0) && (
                <p className="border-t border-line px-4 py-2 text-sm text-muted">
                  {t("explore.zeroPlatformHint")}
                </p>
              )}
            </Panel>
          )}

          <Panel title={t("explore.overTime", { metric: mLabel(chart.metric) })}>
            {chart.points.length === 0 ? (
              <Empty title={t("explore.noData")} hint={t("explore.noDataHint")} />
            ) : (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chart.points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="#232C36" vertical={false} />
                    {/* In Arabic, time reads right-to-left and the value axis
                        moves to the right — otherwise the chart contradicts the
                        page it sits in. */}
                    <XAxis
                      dataKey="date"
                      stroke="#8B98A5"
                      tick={{ fontSize: 11 }}
                      tickMargin={8}
                      reversed={dir === "rtl"}
                    />
                    <YAxis
                      stroke="#8B98A5"
                      tick={{ fontSize: 11 }}
                      width={56}
                      orientation={dir === "rtl" ? "right" : "left"}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#161C23",
                        border: "1px solid #232C36",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      labelStyle={{ color: "#8B98A5" }}
                    />
                    {chart.keys.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
                    {chart.keys.map((k) => (
                      <Line
                        key={k}
                        type="monotone"
                        dataKey={k}
                        name={timeKey ? pName(k) : mLabel(chart.metric)}
                        stroke={timeKey ? platformColor(k) : "#57C4D8"}
                        strokeWidth={2}
                        dot={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </Panel>

          <Panel
            title={t("explore.breakdown")}
            action={
              groupBy.length > 0 && (
                <span className="flex items-center gap-3 text-sm">
                  <span className="eyebrow">{t("common.download")}</span>
                  {(["csv", "xlsx", "json"] as const).map((fmt) => (
                    <a
                      key={fmt}
                      className="text-muted hover:text-ink"
                      href={exportUrl({
                        dataset,
                        date_from: from,
                        date_to: to,
                        format: fmt,
                        group_by: groupBy,
                        ...(dataset === "ads" && platforms.length
                          ? { platform: platforms }
                          : {}),
                      })}
                    >
                      {fmt.toUpperCase()}
                    </a>
                  ))}
                </span>
              )
            }
          >
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="eyebrow">{t("explore.groupBy")}</span>
              {groupOptions.map((g) => (
                <button
                  key={g}
                  onClick={() => toggleGroup(g)}
                  aria-pressed={groupBy.includes(g)}
                  className={`rounded-md border px-2 py-1 text-sm transition-colors ${
                    groupBy.includes(g)
                      ? "border-accent bg-accent/10 text-ink"
                      : "border-line bg-surface text-muted hover:text-ink"
                  }`}
                >
                  {gLabel(g)}
                </button>
              ))}
            </div>

            {groupBy.length === 0 ? (
              <Empty title={t("explore.pickGroup")} hint={t("explore.pickGroupHint")} />
            ) : table.length === 0 ? (
              <Empty title={t("explore.nothingForGrouping")} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-start">
                      {groupBy.map((g) => (
                        <th key={g} className="px-2 py-2 text-start eyebrow">
                          {gLabel(g)}
                        </th>
                      ))}
                      {measures.map((m) => (
                        <th key={m} className="px-2 py-2 text-end eyebrow">
                          {mLabel(m)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {table.map((row, i) => (
                      <tr key={i} className="border-b border-line/60 hover:bg-raised">
                        {groupBy.map((g) => (
                          <td key={g} className="max-w-[22ch] truncate px-2 py-2">
                            {g === "platform" ? (
                              <span className="flex items-center gap-1.5">
                                <PlatformDot platform={String(row[g])} />
                                {pName(String(row[g]))}
                              </span>
                            ) : (
                              (row[g] ?? "—")
                            )}
                          </td>
                        ))}
                        {measures.map((m) => (
                          <td key={m} className="num px-2 py-2 text-end">
                            {measure(m, row[m])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                  {totals && table.length > 0 && (
                    // Total row — uses the ungrouped aggregate (same figures as
                    // the KPI cards), so ratios (ctr/cpc/cpm/cpa/roas) are
                    // recomputed from the sums, never averaged.
                    <tfoot>
                      <tr className="border-t-2 border-line font-medium">
                        <td
                          className="px-2 py-2 eyebrow"
                          colSpan={Math.max(groupBy.length, 1)}
                        >
                          {t("explore.total")}
                        </td>
                        {measures.map((m) => (
                          <td key={m} className="num px-2 py-2 text-end">
                            {measure(m, totals[m])}
                          </td>
                        ))}
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function TagsView({
  snapshot,
  loading,
}: {
  snapshot: GtmSnapshot | null;
  loading: boolean;
}) {
  const { t } = useLocale();
  if (loading) return <Spinner />;
  if (!snapshot?.captured_at || snapshot.data.length === 0) {
    return (
      <Panel title={t("dataset.tags.note")}>
        <Empty title={t("explore.tagsEmpty")} hint={t("explore.tagsEmptyHint")} />
      </Panel>
    );
  }
  return (
    <Panel
      title={t("explore.captured", {
        when: snapshot.captured_at.slice(0, 16).replace("T", " "),
      })}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line">
              <th className="px-2 py-2 text-start eyebrow">{t("explore.kind")}</th>
              <th className="px-2 py-2 text-start eyebrow">{t("explore.name")}</th>
              <th className="px-2 py-2 text-start eyebrow">{t("explore.type")}</th>
              <th className="px-2 py-2 text-start eyebrow">{t("explore.container")}</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.data.map((r, i) => (
              <tr key={i} className="border-b border-line/60 hover:bg-raised">
                <td className="px-2 py-2">
                  <Status value={r.kind} />
                </td>
                <td className="px-2 py-2">{r.name ?? "—"}</td>
                <td className="num px-2 py-2 text-muted">{r.type ?? "—"}</td>
                <td className="num px-2 py-2 text-muted">
                  {r.container_external_id ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
