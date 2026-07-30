"use client";

import { useCallback, useEffect, useState } from "react";

import { ChannelStrip, Empty, ErrorNote, Panel, PlatformDot, Spinner, Status } from "@/components/ui";
import { api, qs } from "@/lib/api";
import { daysAgo } from "@/lib/format";
import { useLocale } from "@/lib/locale";
import type {
  Account,
  Connection,
  Freshness,
  InviteLink,
  MetricPage,
  PlatformInfo,
  Schedule,
} from "@/lib/types";

/** Which dataset each platform feeds — decides where its daily coverage lives. */
const WEB = new Set(["ga4"]);
const SOCIAL = new Set(["meta_social"]);
const CONFIG = new Set(["gtm"]);

const STRIP_FROM = daysAgo(14);
const STRIP_TO = daysAgo(1);

export default function ConnectionsPage() {
  const { t, p: pName } = useLocale();
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [deleted, setDeleted] = useState<Connection[]>([]);
  const [freshness, setFreshness] = useState<Record<number, Freshness>>({});
  const [coverage, setCoverage] = useState<Record<string, Set<string>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, c, a, s, f, d] = await Promise.all([
        api.get<{ platforms: PlatformInfo[] }>("/platforms"),
        api.get<Connection[]>("/connections"),
        api.get<Account[]>("/accounts"),
        api.get<Schedule[]>("/schedules"),
        api.get<Freshness[]>("/freshness"),
        api.get<Connection[]>("/connections?deleted=true"),
      ]);
      setPlatforms(p.platforms);
      setConnections(c);
      setAccounts(a);
      setSchedules(s);
      setDeleted(d);
      setFreshness(Object.fromEntries(f.map((x) => [x.connection_id, x])));

      // Daily coverage for the channel strips: one query per dataset shape,
      // not one per connection.
      const range = { date_from: STRIP_FROM, date_to: STRIP_TO, page_size: 1000 };
      const [ads, web, social] = await Promise.all([
        api.get<MetricPage>(`/metrics${qs({ ...range, group_by: ["platform", "date"] })}`),
        api.get<MetricPage>(`/web-metrics${qs({ ...range, group_by: ["date"] })}`),
        api.get<MetricPage>(`/social-metrics${qs({ ...range, group_by: ["date"] })}`),
      ]);
      const map: Record<string, Set<string>> = {};
      for (const row of ads.data) {
        const key = String(row.platform);
        (map[key] ??= new Set()).add(String(row.date));
      }
      map["ga4"] = new Set(web.data.map((r) => String(r.date)));
      map["meta_social"] = new Set(social.data.map((r) => String(r.date)));
      setCoverage(map);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Spinner label={t("common.loading")} />;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{t("conn.title")}</h1>
          <p className="mt-0.5 text-sm text-muted">{t("conn.subtitle")}</p>
        </div>
        <button className="btn-primary" onClick={() => setAdding((v) => !v)}>
          {adding ? t("common.close") : t("conn.add")}
        </button>
      </header>

      {error && <ErrorNote message={error} />}

      {adding && (
        <AddConnection
          platforms={platforms}
          connected={new Set(connections.map((c) => c.platform))}
          onDone={() => {
            setAdding(false);
            void load();
          }}
        />
      )}

      {connections.length === 0 ? (
        <Panel>
          <Empty title={t("conn.none")} hint={t("conn.noneHint")} />
        </Panel>
      ) : (
        <div className="flex flex-col gap-3">
          {connections.map((c) => (
            <ConnectionCard
              key={c.id}
              connection={c}
              accounts={accounts.filter((a) => a.connection_id === c.id)}
              schedule={schedules.find((s) => s.connection_id === c.id) ?? null}
              days={coverage[c.platform] ?? new Set()}
              fresh={freshness[c.id] ?? null}
              onChange={load}
            />
          ))}
        </div>
      )}

      {deleted.length > 0 && (
        <Panel title={t("conn.removed")}>
          <p className="mb-3 text-sm text-muted">{t("conn.removedHint")}</p>
          <ul className="flex flex-col gap-2">
            {deleted.map((c) => (
              <li key={c.id} className="flex items-center gap-3 text-sm">
                <PlatformDot platform={c.platform} />
                <span className="shrink-0">{pName(c.platform)}</span>
                <span className="min-w-0 truncate text-muted">{c.name}</span>
                <button
                  className="btn-quiet ms-auto"
                  onClick={async () => {
                    await api.post(`/connections/${c.id}/restore`, {});
                    void load();
                  }}
                >
                  {t("conn.restore")}
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function ConnectionCard({
  connection,
  accounts,
  schedule,
  days,
  fresh,
  onChange,
}: {
  connection: Connection;
  accounts: Account[];
  schedule: Schedule | null;
  days: Set<string>;
  fresh: Freshness | null;
  onChange: () => void;
}) {
  const { t, p: pName, ago } = useLocale();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [pickAccounts, setPickAccounts] = useState(false);
  const isConfig = CONFIG.has(connection.platform);

  // Account allow-list: null/empty synced_account_ids means "sync every account".
  const allExternal = accounts.map((a) => a.external_id);
  const pinned = connection.synced_account_ids;
  const syncingAll = !pinned || pinned.length === 0;
  const isOn = (ext: string) => syncingAll || pinned!.includes(ext);
  const onCount = accounts.filter((a) => isOn(a.external_id)).length;

  async function toggleAccount(ext: string) {
    const current = allExternal.filter(isOn);
    const next = current.includes(ext)
      ? current.filter((x) => x !== ext)
      : [...current, ext];
    if (next.length === 0) return; // keep at least one account synced
    setBusy(true);
    setNote(null);
    try {
      // Sending [] means "all"; store the subset otherwise.
      await api.patch(`/connections/${connection.id}/synced-accounts`, {
        account_external_ids: next.length === allExternal.length ? [] : next,
      });
      setNote(t("conn.accountsSaved"));
      onChange();
    } catch (e) {
      setNote(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    setBusy(true);
    setNote(null);
    try {
      await api.post("/syncs", {
        connection_id: connection.id,
        date_from: daysAgo(30),
        date_to: daysAgo(1),
        breakdown: "none",
      });
      setNote(t("conn.syncStarted"));
      onChange();
    } catch (e) {
      setNote(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function createSchedule() {
    setBusy(true);
    try {
      await api.post("/schedules", {
        connection_id: connection.id,
        cadence: "daily",
        hour_utc: 3,
        breakdowns: ["none"],
      });
      onChange();
    } catch (e) {
      setNote(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSchedule() {
    if (!schedule) return;
    setBusy(true);
    try {
      await api.patch(`/schedules/${schedule.id}${qs({ enabled: !schedule.enabled })}`);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-medium">
            <PlatformDot platform={connection.platform} />
            {pName(connection.platform)}
            <Status value={connection.status} />
            {fresh?.stale && (
              <span className="rounded-full border border-bad/40 bg-bad/10 px-2 py-0.5 text-eyebrow text-bad">
                {t("conn.stale", { n: fresh.days_stale ?? 0 })}
              </span>
            )}
          </h3>
          <p className="mt-1 text-sm text-muted">
            {accounts.length > 0
              ? accounts.map((a) => a.name).join(" · ")
              : t("conn.noAccounts")}
          </p>
        </div>

        {!isConfig && (
          <ChannelStrip
            platform={connection.platform}
            days={days}
            from={STRIP_FROM}
            to={STRIP_TO}
          />
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <button className="btn-quiet" onClick={runNow} disabled={busy}>
          {busy ? t("common.working") : t("conn.syncNow")}
        </button>

        {schedule ? (
          <button className="btn-quiet" onClick={toggleSchedule} disabled={busy}>
            {schedule.enabled ? t("conn.pauseDaily") : t("conn.resumeDaily")}
          </button>
        ) : (
          <button className="btn-quiet" onClick={createSchedule} disabled={busy}>
            {t("conn.syncDaily")}
          </button>
        )}

        {!isConfig && accounts.length > 1 && (
          <button
            className="btn-quiet"
            onClick={() => setPickAccounts((v) => !v)}
            disabled={busy}
          >
            {t("conn.chooseAccounts")} ({onCount}/{accounts.length})
          </button>
        )}

        <button
          className="text-sm text-muted hover:text-bad"
          onClick={async () => {
            if (!window.confirm(t("conn.removeConfirm"))) return;
            setBusy(true);
            try {
              await api.del(`/connections/${connection.id}`);
              onChange();
            } finally {
              setBusy(false);
            }
          }}
          disabled={busy}
        >
          {t("conn.remove")}
        </button>

        <span className="ms-auto text-sm text-muted">
          {schedule
            ? t("conn.scheduleState", {
                state: schedule.enabled ? t("conn.daily") : t("conn.paused"),
                when: ago(schedule.last_enqueued_at),
              })
            : t("conn.manualOnly")}
        </span>
      </div>

      {pickAccounts && !isConfig && (
        <div className="mt-3 rounded-md border border-line bg-ground p-3">
          <p className="eyebrow mb-1">
            {syncingAll
              ? t("conn.accountsAll", { m: accounts.length })
              : t("conn.accountsSome", { n: onCount, m: accounts.length })}
          </p>
          <p className="mb-2 text-eyebrow text-muted">{t("conn.accountsHint")}</p>
          <div className="flex flex-col gap-1.5">
            {accounts.map((a) => (
              <label
                key={a.id}
                className="flex items-center gap-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={isOn(a.external_id)}
                  disabled={busy}
                  onChange={() => toggleAccount(a.external_id)}
                />
                <span className="min-w-0 truncate">{a.name}</span>
                {a.currency && (
                  <span className="text-eyebrow text-muted">{a.currency}</span>
                )}
              </label>
            ))}
          </div>
        </div>
      )}

      {note && <p className="mt-2 text-sm text-muted">{note}</p>}
    </article>
  );
}

function AddConnection({
  platforms,
  connected,
  onDone,
}: {
  platforms: PlatformInfo[];
  connected: Set<string>;
  onDone: () => void;
}) {
  const { t, p: pName } = useLocale();
  const [chosen, setChosen] = useState<PlatformInfo | null>(null);
  const [saJson, setSaJson] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inviteMode, setInviteMode] = useState(false);
  const [invite, setInvite] = useState<InviteLink | null>(null);
  const [copied, setCopied] = useState(false);

  async function makeInvite(p: PlatformInfo) {
    setBusy(true);
    setError(null);
    setInvite(null);
    try {
      setInvite(
        await api.post<InviteLink>("/connect-links", {
          platform: p.platform,
          days: 7,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function startOAuth(p: PlatformInfo) {
    setBusy(true);
    setError(null);
    try {
      const { auth_url } = await api.get<{ auth_url: string }>(`/connect/${p.platform}`);
      // The provider redirects back to the backend's callback, which stores the
      // tokens; the person returns here and refreshes.
      window.open(auth_url, "_blank", "noopener");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function submitServiceAccount() {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(saJson);
      await api.post("/connections/service-account", {
        platform: chosen!.platform,
        service_account: parsed,
        property_id: propertyId || null,
      });
      onDone();
    } catch (e) {
      if (e instanceof SyntaxError) setError(t("conn.badJson"));
      else setError(e instanceof Error ? e.message : t("conn.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t("conn.addTitle")}>
      {error && <ErrorNote message={error} />}

      {/* Two ways in, same list of platforms: authorise an account you hold, or
          send the account's owner a link so they authorise it themselves. The
          second is how you onboard a client without touching their password. */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => {
            setInviteMode(false);
            setInvite(null);
          }}
          aria-pressed={!inviteMode}
          className={`btn ${inviteMode ? "btn-quiet" : "btn-primary"}`}
        >
          {t("conn.modeSelf")}
        </button>
        <button
          onClick={() => {
            setInviteMode(true);
            setInvite(null);
          }}
          aria-pressed={inviteMode}
          className={`btn ${inviteMode ? "btn-primary" : "btn-quiet"}`}
        >
          {t("conn.modeInvite")}
        </button>
        <p className="text-sm text-muted">
          {inviteMode ? t("conn.inviteHint") : t("conn.selfHint")}
        </p>
      </div>

      {invite && (
        <div className="mb-3 rounded-md border border-accent/40 bg-accent/10 p-3">
          <p className="eyebrow mb-1.5">
            {t("conn.inviteReady", { platform: pName(invite.platform) })}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              readOnly
              value={invite.url}
              onFocus={(e) => e.currentTarget.select()}
              aria-label={t("conn.inviteReady", {
                platform: pName(invite.platform),
              })}
              className="field min-w-0 flex-1 text-sm"
            />
            <button
              className="btn-quiet"
              onClick={() => {
                void navigator.clipboard?.writeText(invite.url);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
            >
              {copied ? t("common.copied") : t("common.copy")}
            </button>
          </div>
          <p className="mt-2 text-sm text-muted">
            {t("conn.inviteExpiry", { days: invite.expires_in_days })}
          </p>
        </div>
      )}

      <div className="mt-1 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {platforms.map((p) => (
          <button
            key={p.platform}
            onClick={() =>
              p.auth_kind === "oauth"
                ? inviteMode
                  ? makeInvite(p)
                  : startOAuth(p)
                : setChosen(p)
            }
            disabled={busy}
            className={`flex items-center gap-2 rounded-md border px-3 py-2.5 text-start text-sm transition-colors ${
              chosen?.platform === p.platform
                ? "border-accent bg-accent/10"
                : "border-line bg-ground hover:bg-raised"
            }`}
          >
            <PlatformDot platform={p.platform} />
            <span className="min-w-0">
              <span className="block truncate">{pName(p.platform)}</span>
              <span className="block text-eyebrow text-muted">
                {connected.has(p.platform)
                  ? t("conn.connected")
                  : p.auth_kind === "oauth"
                    ? t("conn.authorize")
                    : t("conn.serviceAccount")}
              </span>
            </span>
          </button>
        ))}
      </div>

      {chosen?.auth_kind === "service_account" && (
        <div className="mt-4 flex flex-col gap-3 border-t border-line pt-4">
          <p className="text-sm text-muted">
            {t("conn.saHint", { platform: pName(chosen.platform) })}
          </p>
          <div>
            <label htmlFor="sa" className="eyebrow mb-1.5 block">
              {t("conn.saKey")}
            </label>
            <textarea
              id="sa"
              rows={5}
              className="field font-mono text-xs"
              placeholder='{"type": "service_account", …}'
              value={saJson}
              onChange={(e) => setSaJson(e.target.value)}
            />
          </div>
          {chosen.platform === "ga4" && (
            <div>
              <label htmlFor="prop" className="eyebrow mb-1.5 block">
                {t("conn.propertyId")}
              </label>
              <input
                id="prop"
                className="field"
                placeholder="123456789"
                value={propertyId}
                onChange={(e) => setPropertyId(e.target.value)}
              />
            </div>
          )}
          <button
            className="btn-primary self-start"
            onClick={submitServiceAccount}
            disabled={busy || !saJson}
          >
            {busy ? t("common.working") : t("conn.save")}
          </button>
        </div>
      )}
    </Panel>
  );
}
