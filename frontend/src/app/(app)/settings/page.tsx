"use client";

import { useCallback, useEffect, useState } from "react";

import { Settings as SettingsIcon } from "lucide-react";

import { Empty, ErrorNote, PageHeader, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { Account, ApiKey, FxRate } from "@/lib/types";

type Me = { id: number; email: string; created_at: string };

export default function SettingsPage() {
  const { t, p: pName, ago } = useLocale();
  const [me, setMe] = useState<Me | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [rates, setRates] = useState<FxRate[]>([]);
  const [loading, setLoading] = useState(true);

  const [keyName, setKeyName] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [m, a, k, fx] = await Promise.all([
      api.get<Me>("/auth/me"),
      api.get<Account[]>("/accounts"),
      api.get<ApiKey[]>("/api-keys"),
      api.get<FxRate[]>("/fx-rates"),
    ]);
    setMe(m);
    setAccounts(a);
    setKeys(k);
    setRates(fx);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createKey(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<ApiKey & { key: string }>("/api-keys", {
        name: keyName,
      });
      setFreshKey(created.key);
      setCopied(false);
      setKeyName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("set.keyFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    await api.del(`/api-keys/${id}`);
    await load();
  }

  if (loading) return <Spinner />;

  const live = keys.filter((k) => !k.revoked_at);

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <PageHeader icon={SettingsIcon} title={t("set.title")} subtitle={t("set.subtitle")} />

      <Panel title={t("set.account")}>
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-muted">{t("auth.email")}</dt>
          <dd>{me?.email}</dd>
          <dt className="text-muted">{t("set.joined")}</dt>
          <dd className="num">{me?.created_at?.slice(0, 10)}</dd>
        </dl>
      </Panel>

      <Panel title={t("set.keys", { n: live.length })}>
        <p className="mb-4 text-sm text-muted">{t("set.keysBlurb")}</p>

        {freshKey && (
          <div className="mb-4 rounded-md border border-accent/40 bg-accent/10 p-3">
            <p className="text-sm">{t("set.keyOnce")}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="num flex-1 truncate rounded border border-line bg-ground px-2 py-1.5 text-xs">
                {freshKey}
              </code>
              <button
                className="btn-quiet"
                onClick={() => {
                  void navigator.clipboard.writeText(freshKey);
                  setCopied(true);
                }}
              >
                {copied ? t("set.copied") : t("set.copy")}
              </button>
              <button className="btn-quiet" onClick={() => setFreshKey(null)}>
                {t("set.done")}
              </button>
            </div>
          </div>
        )}

        {error && <ErrorNote message={error} />}

        <form onSubmit={createKey} className="mb-4 flex items-end gap-2">
          <div className="flex-1">
            <label htmlFor="keyname" className="eyebrow mb-1.5 block">
              {t("set.keyPurpose")}
            </label>
            <input
              id="keyname"
              required
              className="field"
              placeholder={t("set.keyPlaceholder")}
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
            />
          </div>
          <button className="btn-primary" disabled={busy || !keyName}>
            {busy ? t("set.creating") : t("set.createKey")}
          </button>
        </form>

        {live.length === 0 ? (
          <Empty title={t("set.noKeys")} />
        ) : (
          <ul className="flex flex-col gap-2">
            {live.map((k) => (
              <li
                key={k.id}
                className="flex items-center gap-3 rounded-md border border-line bg-ground px-3 py-2 text-sm"
              >
                <span className="num text-muted">{k.prefix}…</span>
                <span className="min-w-0 truncate">{k.name}</span>
                <span className="ms-auto text-muted">
                  {k.last_used_at
                    ? t("set.keyUsed", { when: ago(k.last_used_at) })
                    : t("set.keyNeverUsed")}
                </span>
                <button
                  className="text-muted hover:text-bad"
                  onClick={() => revoke(k.id)}
                >
                  {t("set.revoke")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title={t("set.accountsVisible", { n: accounts.length })}>
        {accounts.length === 0 ? (
          <p className="text-sm text-muted">{t("set.noAccounts")}</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {accounts.map((a) => (
              <li key={a.id} className="flex items-center gap-2">
                <PlatformDot platform={a.platform} />
                <span className="min-w-0 truncate">{a.name}</span>
                <span className="num ms-auto text-muted">
                  {a.currency ?? ""} {a.external_id}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <FxRates rates={rates} onChange={load} />
    </div>
  );
}

function FxRates({ rates, onChange }: { rates: FxRate[]; onChange: () => void }) {
  const { t } = useLocale();
  const [ccy, setCcy] = useState("");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api.put("/fx-rates", {
        currency: ccy.toUpperCase(),
        rate_to_usd: Number(rate),
      });
      setCcy("");
      setRate("");
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function autoFetch() {
    setBusy(true);
    try {
      await api.post("/fx-rates/refresh", {});
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function pruneUnused() {
    setBusy(true);
    try {
      await api.post("/fx-rates/prune", {});
      onChange();
    } finally {
      setBusy(false);
    }
  }

  async function remove(ccy: string) {
    setBusy(true);
    try {
      await api.del(`/fx-rates/${ccy}`);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t("set.fxTitle")}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <p className="text-sm text-muted">{t("set.fxBlurb")}</p>
        <div className="flex shrink-0 gap-2">
          <button className="btn-quiet" onClick={pruneUnused} disabled={busy}>
            {busy ? t("common.working") : t("set.fxPrune")}
          </button>
          <button className="btn-quiet" onClick={autoFetch} disabled={busy}>
            {busy ? t("common.working") : t("set.fxFetch")}
          </button>
        </div>
      </div>
      {rates.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1.5 text-sm">
          {rates.map((r) => (
            <li key={r.currency} className="flex items-center gap-2">
              <span className="num w-12">{r.currency}</span>
              <span className="num text-muted">
                1 {r.currency} = {r.rate_to_usd} USD
              </span>
              <button
                className="ms-auto text-muted hover:text-bad"
                onClick={() => remove(r.currency)}
                disabled={busy}
                aria-label={`${t("common.delete")} ${r.currency}`}
              >
                {t("common.delete")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <input
          className="field w-24"
          placeholder="SAR"
          value={ccy}
          onChange={(e) => setCcy(e.target.value)}
        />
        <input
          className="field num w-32"
          type="number"
          step="0.0001"
          placeholder="0.2667"
          value={rate}
          onChange={(e) => setRate(e.target.value)}
        />
        <button
          className="btn-primary"
          onClick={save}
          disabled={busy || !ccy || !rate}
        >
          {busy ? t("common.working") : t("set.fxSave")}
        </button>
      </div>
    </Panel>
  );
}
