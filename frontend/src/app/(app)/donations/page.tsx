"use client";

import { useCallback, useEffect, useState } from "react";

import { HandCoins } from "lucide-react";

import { Empty, ErrorNote, PageHeader, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api, qs } from "@/lib/api";
import { daysAgo } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type { DonationCompare, DonationImport } from "@/lib/types";

const fmt = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined
    ? "—"
    : n.toLocaleString(undefined, { maximumFractionDigits: digits });

// Read a File as text (CSV) or base64 (xlsx) for the JSON import endpoint.
async function readFile(f: File): Promise<{ content?: string; content_base64?: string }> {
  if (/\.xlsx?$|\.xlsm$/i.test(f.name)) {
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) {
      bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    }
    return { content_base64: btoa(bin) };
  }
  return { content: await f.text() };
}

export default function DonationsPage() {
  const { t, p: pName } = useLocale();
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(daysAgo(0));
  const [cmp, setCmp] = useState<DonationCompare | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [mappingText, setMappingText] = useState("");
  const [linksText, setLinksText] = useState("");
  const [preview, setPreview] = useState<DonationImport | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCmp(await api.get<DonationCompare>(`/donations/compare${qs({ date_from: from, date_to: to })}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("don.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [from, to, t]);

  useEffect(() => {
    load();
  }, [load]);

  function parseJson(text: string): Record<string, string> | undefined {
    if (!text.trim()) return undefined;
    return JSON.parse(text);
  }

  async function runImport(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const body = {
        filename: file.name,
        ...(await readFile(file)),
        mapping: parseJson(mappingText),
        link_platforms: parseJson(linksText),
        dry_run: dryRun,
      };
      const res = await api.post<DonationImport>("/donations/import", body);
      setPreview(res);
      if (!dryRun) {
        setNote(t("don.imported", { ins: res.inserted, upd: res.updated }));
        load();
      }
    } catch (e) {
      setError(e instanceof SyntaxError ? t("don.badJson") : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const ccy = cmp?.currency ?? "SAR";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader icon={HandCoins} title={t("don.title")} subtitle={t("don.subtitle")}>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="dfrom" className="eyebrow mb-1 block">{t("don.from")}</label>
            <input id="dfrom" type="date" className="field" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label htmlFor="dto" className="eyebrow mb-1 block">{t("don.to")}</label>
            <input id="dto" type="date" className="field" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
      </PageHeader>

      {error && <ErrorNote message={error} />}

      <Panel title={t("don.compareTitle")}>
        <p className="mb-3 text-sm text-muted">{t("don.compareHint")}</p>
        {loading ? (
          <Spinner />
        ) : !cmp || cmp.rows.length === 0 ? (
          <Empty title={t("don.empty")} hint={t("don.emptyHint")} />
        ) : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="panel p-3">
                <p className="eyebrow">{t("don.ga4Purchases")}</p>
                <p className="num mt-1 text-2xl">{fmt(cmp.totals.ga4_purchases, 0)}</p>
              </div>
              <div className="panel p-3">
                <p className="eyebrow">{t("don.realDonations")}</p>
                <p className="num mt-1 text-2xl">{cmp.totals.jood_orders ? fmt(cmp.totals.jood_orders, 0) : "—"}</p>
              </div>
              <div className="panel p-3">
                <p className="eyebrow">{t("don.claimedAll")}</p>
                <p className="num mt-1 text-2xl">{fmt(cmp.totals.claimed_purchases_all_platforms, 0)}</p>
              </div>
              <div className="panel p-3">
                <p className="eyebrow">{t("don.overclaim")}</p>
                <p className="num mt-1 text-2xl">
                  {(cmp.totals.overclaim ?? cmp.totals.overclaim_vs_ga4) === null
                    ? "—"
                    : `${fmt(cmp.totals.overclaim ?? cmp.totals.overclaim_vs_ga4)}×`}
                </p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line">
                    <th className="px-2 py-2 text-start eyebrow">{t("don.platform")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.spend")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.claimed")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.ga4")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.ga4Ratio")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.real")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.ratio")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.cpd")}</th>
                    <th className="px-2 py-2 text-end eyebrow">{t("don.realRoas")}</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.rows.map((r) => (
                    <tr key={r.platform} className="border-b border-line/60">
                      <td className="px-2 py-2">
                        <span className="inline-flex items-center gap-2">
                          <PlatformDot platform={r.platform} />
                          {r.platform === "unattributed" ? t("don.unattributed") : pName(r.platform)}
                        </span>
                      </td>
                      <td className="num px-2 py-2 text-end">{fmt(r.spend)}</td>
                      <td className="num px-2 py-2 text-end">{fmt(r.claimed_purchases, 0)}</td>
                      <td className="num px-2 py-2 text-end">{fmt(r.ga4_purchases, 0)}</td>
                      <td className={`num px-2 py-2 text-end ${r.ga4_ratio !== null && r.ga4_ratio > 1.5 ? "text-warn" : ""}`}>
                        {r.ga4_ratio === null ? "—" : `${fmt(r.ga4_ratio)}×`}
                      </td>
                      <td className="num px-2 py-2 text-end">
                        {fmt(r.jood_orders, 0)}
                        <span className="text-muted"> · {fmt(r.jood_amount)}</span>
                      </td>
                      <td className={`num px-2 py-2 text-end ${r.claim_ratio !== null && r.claim_ratio > 1.2 ? "text-warn" : ""}`}>
                        {r.claim_ratio === null ? "—" : `${fmt(r.claim_ratio)}×`}
                      </td>
                      <td className="num px-2 py-2 text-end">{fmt(r.cost_per_jood_donation)}</td>
                      <td className="num px-2 py-2 text-end">{fmt(r.jood_roas)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {cmp.warnings.length > 0 && (
              <ul role="status" className="mt-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn">
                {cmp.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
          </>
        )}
      </Panel>

      <Panel title={t("don.importTitle")}>
        <p className="mb-3 text-sm text-muted">{t("don.importHint")}</p>
        <div className="flex flex-col gap-3">
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,text/csv"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setNote(null);
            }}
          />
          <details>
            <summary className="cursor-pointer text-sm text-muted">{t("don.advanced")}</summary>
            <label className="eyebrow mb-1 mt-2 block" htmlFor="map">{t("don.mapping")}</label>
            <textarea id="map" className="field h-20 font-mono text-xs" dir="ltr"
              placeholder='{"order_id": "رقم الطلب", "amount": "المبلغ"}'
              value={mappingText} onChange={(e) => setMappingText(e.target.value)} />
            <label className="eyebrow mb-1 mt-2 block" htmlFor="links">{t("don.links")}</label>
            <textarea id="links" className="field h-20 font-mono text-xs" dir="ltr"
              placeholder='{"t-j.sa/l/snap": "snapchat", "t-j.sa/l/tt": "tiktok"}'
              value={linksText} onChange={(e) => setLinksText(e.target.value)} />
          </details>
          <div className="flex flex-wrap gap-2">
            <button className="btn-quiet" disabled={!file || busy} onClick={() => runImport(true)}>
              {busy ? t("common.working") : t("don.preview")}
            </button>
            <button className="btn-primary" disabled={!file || busy || !preview || preview.valid === 0}
              onClick={() => runImport(false)}>
              {t("don.save")}
            </button>
          </div>
          {note && <p className="text-sm text-good">{note}</p>}
          {preview && (
            <div className="panel p-3 text-sm">
              <p>{t("don.previewLine", { read: preview.rows_read, valid: preview.valid, paid: preview.paid, skipped: preview.skipped_count })}</p>
              {preview.date_range && <p className="text-muted">{preview.date_range[0]} → {preview.date_range[1]}</p>}
              <p className="mt-2 eyebrow">{t("don.detected")}</p>
              <ul className="font-mono text-xs" dir="ltr">
                {Object.entries(preview.mapping).map(([k, v]) => <li key={k}>{k} ← {v}</li>)}
              </ul>
              {preview.unmapped_columns.length > 0 && (
                <p className="mt-1 text-xs text-muted">{t("don.unmapped")}: {preview.unmapped_columns.join(", ")}</p>
              )}
              <p className="mt-2 eyebrow">{t("don.byPlatform")}</p>
              <ul>
                {Object.entries(preview.by_platform).map(([p, v]) => (
                  <li key={p} className="num">{p === "unattributed" ? t("don.unattributed") : pName(p)}: {v.orders} · {fmt(v.amount)}</li>
                ))}
              </ul>
              <p className="mt-2 eyebrow">{t("don.statuses")}</p>
              <p className="text-xs">{Object.entries(preview.statuses).map(([s, n]) => `${s}: ${n}`).join(" · ")}</p>
              {preview.skipped.length > 0 && (
                <p className="mt-2 text-xs text-warn">
                  {preview.skipped.slice(0, 5).map((s) => `#${s.row} ${s.reason}`).join(" · ")}
                </p>
              )}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
