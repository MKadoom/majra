"use client";

import { useCallback, useEffect, useState } from "react";

import { Empty, ErrorNote, Panel, Spinner, Status } from "@/components/ui";
import { api, CURRENCY_CHOICES } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { SavedReport } from "@/lib/types";

type Kind = "google_sheets" | "webhook" | "email";

export default function ReportsPage() {
  const { t, ago } = useLocale();
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setReports(await api.get<SavedReport[]>("/reports"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("rep.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runNow(id: number) {
    try {
      await api.post(`/reports/${id}/run`, {});
    } catch (e) {
      setError(e instanceof Error ? e.message : t("rep.deliverFailed"));
    }
    await load();
  }

  if (loading) return <Spinner label={t("common.loading")} />;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{t("rep.title")}</h1>
          <p className="mt-0.5 text-sm text-muted">{t("rep.subtitle")}</p>
        </div>
        <button className="btn-primary" onClick={() => setAdding((v) => !v)}>
          {adding ? t("common.close") : t("rep.new")}
        </button>
      </header>

      {error && <ErrorNote message={error} />}

      {adding && (
        <NewReport
          onDone={() => {
            setAdding(false);
            void load();
          }}
        />
      )}

      {reports.length === 0 ? (
        <Panel>
          <Empty title={t("rep.none")} hint={t("rep.noneHint")} />
        </Panel>
      ) : (
        <div className="flex flex-col gap-3">
          {reports.map((r) => (
            <article key={r.id} className="panel p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="flex items-center gap-2 font-medium">
                    {r.name}
                    {r.last_status && <Status value={r.last_status} />}
                    {!r.enabled && <Status value="pending" />}
                  </h3>
                  <p className="mt-1 text-sm text-muted">
                    {t("rep.summary", {
                      dataset: t(`dataset.${r.dataset}`),
                      days: String(r.params.last_days ?? 30),
                      cadence: t(`rep.${r.cadence === "weekly" ? "weekly" : "daily"}`),
                      hour: String(r.hour_utc).padStart(2, "0"),
                      dest: t(`rep.dest.${r.destination.kind}`),
                    })}
                    {typeof r.params.currency === "string" && r.params.currency
                      ? ` · ${t("rep.inCurrency", { ccy: r.params.currency })}`
                      : ""}
                  </p>
                  {r.last_error && (
                    <p className="mt-1 text-sm text-bad">{r.last_error}</p>
                  )}
                </div>
                <span className="text-sm text-muted">
                  {r.last_run_at
                    ? t("rep.ran", { when: ago(r.last_run_at) })
                    : t("rep.neverRun")}
                </span>
              </div>

              <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
                <button className="btn-quiet" onClick={() => runNow(r.id)}>
                  {t("rep.runNow")}
                </button>
                <button
                  className="btn-quiet"
                  onClick={async () => {
                    await api.patch(`/reports/${r.id}?enabled=${!r.enabled}`);
                    await load();
                  }}
                >
                  {r.enabled ? t("rep.pause") : t("rep.resume")}
                </button>
                <button
                  className="btn-quiet ms-auto text-muted"
                  onClick={async () => {
                    await api.del(`/reports/${r.id}`);
                    await load();
                  }}
                >
                  {t("rep.delete")}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function NewReport({ onDone }: { onDone: () => void }) {
  const { t } = useLocale();
  const [name, setName] = useState("");
  const [dataset, setDataset] = useState<"ads" | "web" | "social">("ads");
  const [lastDays, setLastDays] = useState(30);
  const [hour, setHour] = useState(6);
  const [cadence, setCadence] = useState<"daily" | "weekly">("daily");
  const [currency, setCurrency] = useState("SAR");
  const [total, setTotal] = useState(true);
  const [kind, setKind] = useState<Kind>("google_sheets");

  const [sheetId, setSheetId] = useState("");
  const [sheetName, setSheetName] = useState("majra");
  const [saJson, setSaJson] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [to, setTo] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let destination: Record<string, unknown>;
      if (kind === "google_sheets") {
        destination = {
          kind,
          spreadsheet_id: sheetId,
          sheet_name: sheetName || "majra",
          service_account: JSON.parse(saJson),
        };
      } else if (kind === "webhook") {
        destination = { kind, url, secret: secret || undefined };
      } else {
        destination = { kind, to };
      }

      await api.post("/reports", {
        name,
        dataset,
        params: {
          last_days: lastDays,
          group_by: [dataset === "ads" ? "platform" : "date"],
          total,
          // Only ads convert; "" means leave each account in its own currency.
          ...(dataset === "ads" && currency ? { currency } : {}),
        },
        destination,
        cadence,
        hour_utc: hour,
      });
      onDone();
    } catch (err) {
      if (err instanceof SyntaxError) setError(t("rep.badSaJson"));
      else setError(err instanceof Error ? err.message : t("rep.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t("rep.new")}>
      <form onSubmit={save} className="flex flex-col gap-4">
        {error && <ErrorNote message={error} />}

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="rname" className="eyebrow mb-1.5 block">
              {t("rep.name")}
            </label>
            <input
              id="rname"
              required
              className="field"
              placeholder={t("rep.namePlaceholder")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="rdataset" className="eyebrow mb-1.5 block">
              {t("rep.data")}
            </label>
            <select
              id="rdataset"
              className="field"
              value={dataset}
              onChange={(e) => setDataset(e.target.value as typeof dataset)}
            >
              <option value="ads">{t("dataset.ads")}</option>
              <option value="web">{t("dataset.web")}</option>
              <option value="social">{t("dataset.social")}</option>
            </select>
          </div>
          <div>
            <label htmlFor="rdays" className="eyebrow mb-1.5 block">
              {t("rep.window")}
            </label>
            <input
              id="rdays"
              type="number"
              min={1}
              max={365}
              className="field"
              value={lastDays}
              onChange={(e) => setLastDays(Number(e.target.value))}
            />
          </div>
          <div>
            <label htmlFor="rhour" className="eyebrow mb-1.5 block">
              {t("rep.sendAt")}
            </label>
            <input
              id="rhour"
              type="number"
              min={0}
              max={23}
              className="field"
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
            />
          </div>
          <div>
            <label htmlFor="rcadence" className="eyebrow mb-1.5 block">
              {t("rep.cadence")}
            </label>
            <select
              id="rcadence"
              className="field"
              value={cadence}
              onChange={(e) => setCadence(e.target.value as typeof cadence)}
            >
              <option value="daily">{t("rep.daily")}</option>
              <option value="weekly">{t("rep.weekly")}</option>
            </select>
          </div>
          {dataset === "ads" && (
            <div>
              <label htmlFor="rccy" className="eyebrow mb-1.5 block">
                {t("rep.currency")}
              </label>
              <select
                id="rccy"
                className="field"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                <option value="">{t("rep.currencyRaw")}</option>
                {CURRENCY_CHOICES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={total}
            onChange={(e) => setTotal(e.target.checked)}
          />
          <span>{t("rep.totals")}</span>
          <span className="text-muted">— {t("rep.totalsHint")}</span>
        </label>

        <div>
          <span className="eyebrow mb-1.5 block">{t("rep.deliverTo")}</span>
          <div className="flex flex-wrap gap-1.5">
            {(["google_sheets", "webhook", "email"] as Kind[]).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                aria-pressed={kind === k}
                className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                  kind === k
                    ? "border-accent bg-accent/10 text-ink"
                    : "border-line bg-surface text-muted hover:text-ink"
                }`}
              >
                {t(`rep.dest.${k}`)}
              </button>
            ))}
          </div>
        </div>

        {kind === "google_sheets" && (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted">{t("rep.sheetHint")}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="sid" className="eyebrow mb-1.5 block">
                  {t("rep.spreadsheetId")}
                </label>
                <input
                  id="sid"
                  required
                  className="field"
                  placeholder="1AbC…"
                  value={sheetId}
                  onChange={(e) => setSheetId(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="sname" className="eyebrow mb-1.5 block">
                  {t("rep.tabName")}
                </label>
                <input
                  id="sname"
                  className="field"
                  value={sheetName}
                  onChange={(e) => setSheetName(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label htmlFor="sa" className="eyebrow mb-1.5 block">
                {t("conn.saKey")}
              </label>
              <textarea
                id="sa"
                required
                rows={4}
                className="field font-mono text-xs"
                placeholder='{"type": "service_account", …}'
                value={saJson}
                onChange={(e) => setSaJson(e.target.value)}
              />
            </div>
          </div>
        )}

        {kind === "webhook" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="url" className="eyebrow mb-1.5 block">
                {t("rep.url")}
              </label>
              <input
                id="url"
                required
                type="url"
                className="field"
                placeholder="https://example.com/hooks/majra"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="secret" className="eyebrow mb-1.5 block">
                {t("rep.secret")}
              </label>
              <input
                id="secret"
                className="field"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
              <p className="mt-1.5 text-eyebrow text-muted">{t("rep.secretHint")}</p>
            </div>
          </div>
        )}

        {kind === "email" && (
          <div>
            <label htmlFor="to" className="eyebrow mb-1.5 block">
              {t("rep.sendToLabel")}
            </label>
            <input
              id="to"
              required
              type="email"
              className="field"
              placeholder="you@example.com"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
            <p className="mt-1.5 text-eyebrow text-muted">{t("rep.smtpHint")}</p>
          </div>
        )}

        <button className="btn-primary self-start" disabled={busy}>
          {busy ? t("common.working") : t("rep.save")}
        </button>
      </form>
    </Panel>
  );
}
