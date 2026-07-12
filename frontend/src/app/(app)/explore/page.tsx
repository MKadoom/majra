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
  platformColor,
  platformLabel,
  qs,
} from "@/lib/api";
import { daysAgo, downloadCsv, formatMeasure, isoDate, label } from "@/lib/format";
import type { GtmSnapshot, MetricPage, MetricRow } from "@/lib/types";

const DATASETS: { key: Dataset; label: string; note: string }[] = [
  { key: "ads", label: "Ads", note: "Meta · Google · TikTok · Snapchat" },
  { key: "web", label: "Web", note: "Google Analytics 4" },
  { key: "social", label: "Social", note: "Facebook & Instagram" },
  { key: "tags", label: "Tags", note: "Tag Manager" },
];

export default function ExplorePage() {
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
      setError(e instanceof Error ? e.message : "Could not load these numbers.");
    } finally {
      setLoading(false);
    }
  }, [dataset, from, to, platforms, groupBy, measures, timeKey]);

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
          <h1 className="text-xl font-semibold tracking-tight">Explore</h1>
          <p className="mt-0.5 text-sm text-muted">
            {DATASETS.find((d) => d.key === dataset)?.note}
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label htmlFor="from" className="eyebrow mb-1 block">
              From
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
              To
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
            key={d.key}
            role="tab"
            aria-selected={dataset === d.key}
            onClick={() => {
              setDataset(d.key);
              setGroupBy([]);
            }}
            className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
              dataset === d.key
                ? "border-accent bg-accent/10 text-ink"
                : "border-line bg-surface text-muted hover:text-ink"
            }`}
          >
            {d.label}
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
              <span className="eyebrow">Platforms</span>
              {AD_PLATFORMS.map((p) => {
                const on = platforms.length === 0 || platforms.includes(p);
                return (
                  <button
                    key={p}
                    onClick={() => togglePlatform(p)}
                    aria-pressed={platforms.includes(p)}
                    className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm transition-colors ${
                      on
                        ? "border-line bg-surface text-ink"
                        : "border-line bg-surface text-muted/50"
                    }`}
                  >
                    <PlatformDot platform={p} />
                    {platformLabel(p)}
                  </button>
                );
              })}
              {platforms.length > 0 && (
                <button
                  onClick={() => setPlatforms([])}
                  className="text-sm text-muted hover:text-ink"
                >
                  Show all
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
                  <p className="eyebrow">{label(m)}</p>
                  <p className="num mt-1.5 text-xl">
                    {totals ? formatMeasure(m, totals[m]) : "—"}
                  </p>
                </div>
              ))}
            </div>
          )}

          <Panel title={`${label(chart.metric)} over time`}>
            {chart.points.length === 0 ? (
              <Empty
                title="No data in this range."
                hint="Connect a platform and run a sync, or widen the dates."
              />
            ) : (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chart.points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="#232C36" vertical={false} />
                    <XAxis
                      dataKey="date"
                      stroke="#8B98A5"
                      tick={{ fontSize: 11 }}
                      tickMargin={8}
                    />
                    <YAxis stroke="#8B98A5" tick={{ fontSize: 11 }} width={56} />
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
                        name={timeKey ? platformLabel(k) : label(chart.metric)}
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
            title="Breakdown"
            action={
              table.length > 0 && (
                <button
                  className="text-sm text-muted hover:text-ink"
                  onClick={() => downloadCsv(table, `majra-${dataset}-${from}_${to}.csv`)}
                >
                  Download CSV
                </button>
              )
            }
          >
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="eyebrow">Group by</span>
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
                  {label(g)}
                </button>
              ))}
            </div>

            {groupBy.length === 0 ? (
              <Empty
                title="Pick something to group by."
                hint="Campaign, platform, country — whatever you want to compare."
              />
            ) : table.length === 0 ? (
              <Empty title="Nothing to show for this grouping." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-start">
                      {groupBy.map((g) => (
                        <th key={g} className="px-2 py-2 text-start eyebrow">
                          {label(g)}
                        </th>
                      ))}
                      {measures.map((m) => (
                        <th key={m} className="px-2 py-2 text-end eyebrow">
                          {label(m)}
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
                                {platformLabel(String(row[g]))}
                              </span>
                            ) : (
                              (row[g] ?? "—")
                            )}
                          </td>
                        ))}
                        {measures.map((m) => (
                          <td key={m} className="num px-2 py-2 text-end">
                            {formatMeasure(m, row[m])}
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
  if (loading) return <Spinner />;
  if (!snapshot?.captured_at || snapshot.data.length === 0) {
    return (
      <Panel title="Tag Manager">
        <Empty
          title="No Tag Manager snapshot yet."
          hint="Connect Tag Manager and run a sync to capture your container."
        />
      </Panel>
    );
  }
  return (
    <Panel title={`Tag Manager · captured ${snapshot.captured_at.slice(0, 16).replace("T", " ")}`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line">
              <th className="px-2 py-2 text-start eyebrow">Kind</th>
              <th className="px-2 py-2 text-start eyebrow">Name</th>
              <th className="px-2 py-2 text-start eyebrow">Type</th>
              <th className="px-2 py-2 text-start eyebrow">Container</th>
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
