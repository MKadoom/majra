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
  api,
  exportUrl,
  platformColor,
  qs,
} from "@/lib/api";
import { daysAgo, isoDate } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type { GtmSnapshot, MetricPage, MetricRow } from "@/lib/types";

const DATASETS: Dataset[] = ["ads", "web", "social", "tags"];

export default function ExplorePage() {
  const { t, m: mLabel, g: gLabel, p: pName, measure, dir } = useLocale();
  const [dataset, setDataset] = useState<Dataset>("ads");
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(daysAgo(1));
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<string[]>([]);

  const [totals, setTotals] = useState<MetricRow | null>(null);
  const [series, setSeries] = useState<MetricRow[]>([]);
  const [table, setTable] = useState<MetricRow[]>([]);
  const [tags, setTags] = useState<GtmSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
    } catch (e) {
      setError(e instanceof Error ? e.message : t("explore.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [dataset, from, to, platforms, groupBy, measures, timeKey, t]);

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
    const sums = new Map<string, { value: number; rows: number }>();
    for (const row of series) {
      const key = String(row[timeKey] ?? "");
      if (!key) continue;
      const cur = sums.get(key) ?? { value: 0, rows: 0 };
      cur.value += Number(row[metric] ?? 0);
      cur.rows += 1;
      sums.set(key, cur);
    }
    const total = [...sums.values()].reduce((a, b) => a + b.value, 0);
    return [...sums.entries()]
      .map(([platform, { value, rows }]) => ({
        platform,
        value,
        rows,
        share: total > 0 ? value / total : 0,
      }))
      .sort((a, b) => b.value - a.value);
  }, [series, dataset, timeKey, measures]);

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
                          : "border-line/60 bg-transparent text-muted/60 line-through decoration-muted/40"
                    }`}
                  >
                    <span className={state === "off" ? "opacity-40" : ""}>
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

          {/* Headline numbers */}
          {loading && !totals ? (
            <Spinner />
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {measures.slice(0, 8).map((m) => (
                <div key={m} className="panel p-4">
                  <p className="eyebrow">{mLabel(m)}</p>
                  <p className="num mt-1.5 text-xl">
                    {totals ? measure(m, totals[m]) : "—"}
                  </p>
                </div>
              ))}
            </div>
          )}

          {/* Which channel produced the headline number. One row per platform,
              with a share bar in that platform's own hue. A platform that
              returned rows but no spend says so plainly, rather than silently
              contributing nothing to a blended total. */}
          {!loading && byPlatform.length > 0 && (
            <Panel title={t("explore.byPlatform", { metric: mLabel(chart.metric) })}>
              <ul className="divide-y divide-line">
                {byPlatform.map(({ platform, value, share, rows }) => (
                  <li
                    key={platform}
                    className="flex items-center gap-3 px-4 py-2.5"
                  >
                    <span className="flex min-w-[7.5rem] items-center gap-2">
                      <PlatformDot platform={platform} />
                      <span className="truncate text-sm">{pName(platform)}</span>
                    </span>
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
              {byPlatform.some((p) => p.value === 0) && (
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
