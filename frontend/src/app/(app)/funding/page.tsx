"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Wallet } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Empty, ErrorNote, PageHeader, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api, currencyOptions, platformColor, qs } from "@/lib/api";
import { daysAgo } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type { CardCharge, Funding, MetricPage } from "@/lib/types";

const money = (n: number, ccy: string) =>
  `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${ccy}`;

const isoLocal = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;

type Preset = "last7" | "last30" | "last60" | "last90" | "mtd" | "lastmonth" | "custom";

// Resolve a preset (or the custom inputs) into an inclusive from/to window.
function rangeFor(preset: Preset, from: string, to: string): { from: string; to: string } {
  const now = new Date();
  if (preset === "custom") return { from, to };
  if (preset === "mtd") {
    return { from: isoLocal(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoLocal(now) };
  }
  if (preset === "lastmonth") {
    return {
      from: isoLocal(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
      to: isoLocal(new Date(now.getFullYear(), now.getMonth(), 0)),
    };
  }
  const n = { last7: 7, last30: 30, last60: 60, last90: 90 }[preset];
  return { from: daysAgo(n - 1), to: daysAgo(0) };
}

export default function FundingPage() {
  const { t, p: pName, dir } = useLocale();
  const [currency, setCurrency] = useState("USD");
  const [fxCurrencies, setFxCurrencies] = useState<string[]>(["USD"]);
  const [granularity, setGranularity] = useState<"day" | "month">("day");
  const [preset, setPreset] = useState<Preset>("last30");
  const [customFrom, setCustomFrom] = useState(daysAgo(29));
  const [customTo, setCustomTo] = useState(daysAgo(0));
  const [months, setMonths] = useState(12);
  const [funding, setFunding] = useState<Funding | null>(null);
  const [charges, setCharges] = useState<CardCharge[]>([]);
  const [perPlatform, setPerPlatform] = useState<MetricPage["data"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ currency: string }[]>("/fx-rates")
      .then((rs) => {
        const opts = currencyOptions(rs.map((r) => r.currency));
        setFxCurrencies(opts);
        // Prefer SAR (this instance's reporting currency), else USD.
        setCurrency(opts.includes("SAR") ? "SAR" : "USD");
      })
      .catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = rangeFor(preset, customFrom, customTo);
      const [f, c, m] = await Promise.all([
        api.get<Funding>(
          `/funding${qs({ currency, date_from: r.from, date_to: r.to, months })}`,
        ),
        api.get<CardCharge[]>("/card-charges"),
        api.get<MetricPage>(
          `/metrics${qs({
            date_from: r.from,
            date_to: r.to,
            group_by: ["date", "platform"],
            currency,
            page_size: 1000,
          })}`,
        ),
      ]);
      setFunding(f);
      setCharges(c);
      setPerPlatform(m.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [currency, preset, customFrom, customTo, months, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const { dates, platforms, cell, dayTotal, dayCharged } = useMemo(() => {
    const byDate = new Map<string, Record<string, number>>();
    const plats = new Set<string>();
    for (const r of perPlatform) {
      const d = String(r.date);
      const p = String(r.platform);
      plats.add(p);
      let rec = byDate.get(d);
      if (!rec) {
        rec = {};
        byDate.set(d, rec);
      }
      rec[p] = Number(r.spend ?? 0);
    }
    const chargedByDate: Record<string, number> = {};
    const spendByDate: Record<string, number> = {};
    for (const dd of funding?.daily ?? []) {
      chargedByDate[dd.date] = dd.charged;
      spendByDate[dd.date] = dd.spend;
    }
    const allDates = [...new Set([...byDate.keys(), ...Object.keys(chargedByDate)])]
      .sort()
      .reverse();
    return {
      dates: allDates,
      platforms: [...plats].sort(),
      cell: (d: string, p: string) => byDate.get(d)?.[p] ?? 0,
      dayTotal: (d: string) => spendByDate[d] ?? 0,
      dayCharged: (d: string) => chargedByDate[d] ?? 0,
    };
  }, [perPlatform, funding]);

  const monthly = funding?.monthly ?? [];
  const monthPlatforms = useMemo(() => {
    const s = new Set<string>();
    for (const m of monthly) for (const p of Object.keys(m.by_platform)) s.add(p);
    return [...s].sort();
  }, [monthly]);

  // Spend-over-time chart (oldest → newest, RTL-aware), stacked by platform —
  // the shape ad managers use. Reuses the same day/month split as the table.
  const chart = useMemo(() => {
    if (granularity === "day") {
      const points = [...dates].reverse().map((d) => {
        const row: Record<string, number | string> = { label: d };
        for (const p of platforms) row[p] = Number(cell(d, p).toFixed(2));
        return row;
      });
      return { points, keys: platforms };
    }
    const points = [...monthly].reverse().map((m) => {
      const row: Record<string, number | string> = { label: m.month };
      for (const p of monthPlatforms) row[p] = m.by_platform[p] ?? 0;
      return row;
    });
    return { points, keys: monthPlatforms };
  }, [granularity, dates, platforms, cell, monthly, monthPlatforms]);

  // Column/row totals for the visible table — guaranteed to match the rows a
  // user is looking at, so the summary card and footer always reconcile.
  const dailyTotals = useMemo(() => {
    const per: Record<string, number> = {};
    let spend = 0;
    let charged = 0;
    for (const d of dates) {
      for (const p of platforms) per[p] = (per[p] ?? 0) + cell(d, p);
      spend += dayTotal(d);
      charged += dayCharged(d);
    }
    return { per, spend, charged };
  }, [dates, platforms, cell, dayTotal, dayCharged]);

  const monthlyTotals = useMemo(() => {
    const per: Record<string, number> = {};
    let spend = 0;
    let charged = 0;
    let net = 0;
    for (const m of monthly) {
      for (const p of monthPlatforms) per[p] = (per[p] ?? 0) + (m.by_platform[p] ?? 0);
      spend += m.spend;
      charged += m.charged;
      net += m.net;
    }
    return { per, spend, charged, net };
  }, [monthly, monthPlatforms]);

  if (loading && !funding) return <Spinner label={t("common.loading")} />;
  const ccy = funding?.currency ?? currency;
  const low = (funding?.balance ?? 0) <= 0;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Wallet} title={t("fund.title")} subtitle={t("fund.subtitle")}>
        <div className="flex flex-wrap items-end gap-2">
          <div className="inline-flex overflow-hidden rounded-md border border-line">
            {(["day", "month"] as const).map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => setGranularity(g)}
                aria-pressed={granularity === g}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  granularity === g
                    ? "bg-accent/10 text-ink"
                    : "text-muted hover:bg-raised hover:text-ink"
                }`}
              >
                {g === "day" ? t("fund.viewDaily") : t("fund.viewMonthly")}
              </button>
            ))}
          </div>
          <select
            className="field"
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            aria-label={t("explore.currency")}
          >
            {fxCurrencies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          {granularity === "day" ? (
            <>
              <select
                className="field"
                value={preset}
                onChange={(e) => setPreset(e.target.value as Preset)}
                aria-label={t("fund.range")}
              >
                <option value="last7">{t("fund.lastDays", { n: 7 })}</option>
                <option value="last30">{t("fund.lastDays", { n: 30 })}</option>
                <option value="last60">{t("fund.lastDays", { n: 60 })}</option>
                <option value="last90">{t("fund.lastDays", { n: 90 })}</option>
                <option value="mtd">{t("fund.mtd")}</option>
                <option value="lastmonth">{t("fund.lastMonth")}</option>
                <option value="custom">{t("fund.custom")}</option>
              </select>
              {preset === "custom" && (
                <>
                  <input
                    className="field"
                    type="date"
                    value={customFrom}
                    max={customTo}
                    onChange={(e) => setCustomFrom(e.target.value)}
                    aria-label={t("fund.from")}
                  />
                  <input
                    className="field"
                    type="date"
                    value={customTo}
                    min={customFrom}
                    onChange={(e) => setCustomTo(e.target.value)}
                    aria-label={t("fund.to")}
                  />
                </>
              )}
            </>
          ) : (
            <select
              className="field"
              value={months}
              onChange={(e) => setMonths(Number(e.target.value))}
              aria-label={t("fund.range")}
            >
              {[6, 12, 24].map((m) => (
                <option key={m} value={m}>
                  {t("fund.lastMonths", { n: m })}
                </option>
              ))}
            </select>
          )}
        </div>
      </PageHeader>

      {error && <ErrorNote message={error} />}

      {funding && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="panel p-4">
            <p className="eyebrow text-muted">{t("fund.spent")}</p>
            <p className="num mt-1 text-2xl">
              {money(granularity === "day" ? funding.range_spent : monthlyTotals.spend, ccy)}
            </p>
            <p className="mt-1 text-xs text-muted">
              {t("fund.allTime")}: <span className="num">{money(funding.total_spent, ccy)}</span>
            </p>
          </div>
          <div className="panel p-4">
            <p className="eyebrow text-muted">{t("fund.charged")}</p>
            <p className="num mt-1 text-2xl">
              {money(granularity === "day" ? funding.range_charged : monthlyTotals.charged, ccy)}
            </p>
            <p className="mt-1 text-xs text-muted">
              {t("fund.allTime")}: <span className="num">{money(funding.total_charged, ccy)}</span>
            </p>
          </div>
          <div className={`panel p-4 ${low ? "border-bad/50" : "border-good/50"}`}>
            <p className="eyebrow text-muted">{t("fund.balance")}</p>
            <p className={`num mt-1 text-2xl ${low ? "text-bad" : "text-good"}`}>
              {money(funding.balance, ccy)}
            </p>
            <p className="mt-1 text-xs text-muted">{t("fund.allTime")}</p>
          </div>
        </div>
      )}

      <Panel title={granularity === "day" ? t("fund.daily") : t("fund.monthly")}>
        {chart.points.length === 0 ? (
          <Empty title={granularity === "day" ? t("fund.noDaily") : t("fund.noMonthly")} />
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart.points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#232C36" vertical={false} />
                <XAxis
                  dataKey="label"
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
                  cursor={{ fill: "#232C3640" }}
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
                  <Bar
                    key={k}
                    dataKey={k}
                    name={pName(k)}
                    stackId="spend"
                    fill={platformColor(k)}
                    maxBarSize={48}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>

      {granularity === "day" ? (
        <Panel title={t("fund.breakdown")}>
          {dates.length === 0 ? (
            <Empty title={t("fund.noDaily")} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line">
                    <th className="px-2 py-2 text-start eyebrow">{t("fund.date")}</th>
                    {platforms.map((p) => (
                      <th key={p} className="px-2 py-2 text-end eyebrow">
                        {pName(p)}
                      </th>
                    ))}
                    <th className="px-2 py-2 text-end eyebrow">{t("explore.total")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("fund.chargedCol")}</th>
                  </tr>
                </thead>
                <tbody>
                  {dates.map((d) => (
                    <tr key={d} className="border-b border-line/60 hover:bg-raised">
                      <td className="px-2 py-2">{d}</td>
                      {platforms.map((p) => (
                        <td key={p} className="num px-2 py-2 text-end">
                          {cell(d, p).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                        </td>
                      ))}
                      <td className="num px-2 py-2 text-end font-medium">
                        {dayTotal(d).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </td>
                      <td className="num px-2 py-2 text-end text-good">
                        {dayCharged(d)
                          ? dayCharged(d).toLocaleString(undefined, {
                              maximumFractionDigits: 2,
                            })
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-line bg-raised/40 font-medium">
                    <td className="px-2 py-2 eyebrow">{t("fund.rangeTotal")}</td>
                    {platforms.map((p) => (
                      <td key={p} className="num px-2 py-2 text-end">
                        {(dailyTotals.per[p] ?? 0).toLocaleString(undefined, {
                          maximumFractionDigits: 2,
                        })}
                      </td>
                    ))}
                    <td className="num px-2 py-2 text-end">
                      {dailyTotals.spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="num px-2 py-2 text-end text-good">
                      {dailyTotals.charged
                        ? dailyTotals.charged.toLocaleString(undefined, {
                            maximumFractionDigits: 2,
                          })
                        : "—"}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Panel>
      ) : (
        <Panel title={t("fund.breakdown")}>
          {monthly.length === 0 ? (
            <Empty title={t("fund.noMonthly")} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line">
                    <th className="px-2 py-2 text-start eyebrow">{t("fund.month")}</th>
                    {monthPlatforms.map((p) => (
                      <th key={p} className="px-2 py-2 text-end eyebrow">
                        {pName(p)}
                      </th>
                    ))}
                    <th className="px-2 py-2 text-end eyebrow">{t("explore.total")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("fund.chargedCol")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("fund.net")}</th>
                  </tr>
                </thead>
                <tbody>
                  {monthly.map((m) => (
                    <tr key={m.month} className="border-b border-line/60 hover:bg-raised">
                      <td className="num px-2 py-2">{m.month}</td>
                      {monthPlatforms.map((p) => (
                        <td key={p} className="num px-2 py-2 text-end">
                          {(m.by_platform[p] ?? 0).toLocaleString(undefined, {
                            maximumFractionDigits: 2,
                          })}
                        </td>
                      ))}
                      <td className="num px-2 py-2 text-end font-medium">
                        {m.spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </td>
                      <td className="num px-2 py-2 text-end text-good">
                        {m.charged
                          ? m.charged.toLocaleString(undefined, { maximumFractionDigits: 2 })
                          : "—"}
                      </td>
                      <td
                        className={`num px-2 py-2 text-end font-medium ${
                          m.net < 0 ? "text-bad" : "text-good"
                        }`}
                      >
                        {m.net.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-line bg-raised/40 font-medium">
                    <td className="px-2 py-2 eyebrow">{t("fund.rangeTotal")}</td>
                    {monthPlatforms.map((p) => (
                      <td key={p} className="num px-2 py-2 text-end">
                        {(monthlyTotals.per[p] ?? 0).toLocaleString(undefined, {
                          maximumFractionDigits: 2,
                        })}
                      </td>
                    ))}
                    <td className="num px-2 py-2 text-end">
                      {monthlyTotals.spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className="num px-2 py-2 text-end text-good">
                      {monthlyTotals.charged
                        ? monthlyTotals.charged.toLocaleString(undefined, {
                            maximumFractionDigits: 2,
                          })
                        : "—"}
                    </td>
                    <td
                      className={`num px-2 py-2 text-end ${
                        monthlyTotals.net < 0 ? "text-bad" : "text-good"
                      }`}
                    >
                      {monthlyTotals.net.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Panel>
      )}

      <AddCharge defaultCurrency={ccy} onDone={load} />

      <Panel title={t("fund.topups")}>
        {charges.length === 0 ? (
          <Empty title={t("fund.noCharges")} />
        ) : (
          <ul className="flex flex-col gap-1.5 text-sm">
            {charges.map((c) => (
              <li key={c.id} className="flex items-center gap-3">
                <span className="num w-28">{c.date}</span>
                <span className="num">{money(c.amount, c.currency)}</span>
                {c.note && <span className="text-muted">{c.note}</span>}
                <button
                  className="ms-auto text-muted hover:text-bad"
                  onClick={async () => {
                    await api.del(`/card-charges/${c.id}`);
                    void load();
                  }}
                >
                  {t("common.delete")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function AddCharge({
  defaultCurrency,
  onDone,
}: {
  defaultCurrency: string;
  onDone: () => void;
}) {
  const { t } = useLocale();
  const [date, setDate] = useState(daysAgo(0));
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api.post("/card-charges", {
        date,
        amount: Number(amount),
        currency: currency.toUpperCase(),
        note: note || null,
      });
      setAmount("");
      setNote("");
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t("fund.addTitle")}>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <input
          className="field"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
        <input
          className="field num"
          type="number"
          placeholder={t("fund.amount")}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <input
          className="field"
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
        />
        <input
          className="field"
          placeholder={t("fund.note")}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      <button
        className="btn-primary mt-3 self-start"
        onClick={save}
        disabled={busy || !amount}
      >
        {busy ? t("common.working") : t("fund.add")}
      </button>
    </Panel>
  );
}
