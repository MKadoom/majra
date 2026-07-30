"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Wallet } from "lucide-react";

import { Empty, ErrorNote, PageHeader, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api, currencyOptions, qs } from "@/lib/api";
import { daysAgo } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type { CardCharge, Funding, MetricPage } from "@/lib/types";

const money = (n: number, ccy: string) =>
  `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${ccy}`;

export default function FundingPage() {
  const { t, p: pName } = useLocale();
  const [currency, setCurrency] = useState("USD");
  const [fxCurrencies, setFxCurrencies] = useState<string[]>(["USD"]);
  const [days, setDays] = useState(30);
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
      const [f, c, m] = await Promise.all([
        api.get<Funding>(`/funding${qs({ currency, days })}`),
        api.get<CardCharge[]>("/card-charges"),
        api.get<MetricPage>(
          `/metrics${qs({
            date_from: daysAgo(days - 1),
            date_to: daysAgo(0),
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
  }, [currency, days, t]);

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

  if (loading && !funding) return <Spinner label={t("common.loading")} />;
  const ccy = funding?.currency ?? currency;
  const low = (funding?.balance ?? 0) <= 0;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Wallet} title={t("fund.title")} subtitle={t("fund.subtitle")}>
        <div className="flex flex-wrap items-end gap-2">
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
          <select
            className="field"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            aria-label={t("fund.range")}
          >
            {[30, 60, 90].map((d) => (
              <option key={d} value={d}>
                {t("fund.lastDays", { n: d })}
              </option>
            ))}
          </select>
        </div>
      </PageHeader>

      {error && <ErrorNote message={error} />}

      {funding && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="panel p-4">
            <p className="eyebrow text-muted">{t("fund.charged")}</p>
            <p className="num mt-1 text-2xl">{money(funding.total_charged, ccy)}</p>
          </div>
          <div className="panel p-4">
            <p className="eyebrow text-muted">{t("fund.spent")}</p>
            <p className="num mt-1 text-2xl">{money(funding.total_spent, ccy)}</p>
          </div>
          <div className={`panel p-4 ${low ? "border-bad/50" : "border-good/50"}`}>
            <p className="eyebrow text-muted">{t("fund.balance")}</p>
            <p className={`num mt-1 text-2xl ${low ? "text-bad" : "text-good"}`}>
              {money(funding.balance, ccy)}
            </p>
          </div>
        </div>
      )}

      <Panel title={t("fund.daily")}>
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
            </table>
          </div>
        )}
      </Panel>

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
