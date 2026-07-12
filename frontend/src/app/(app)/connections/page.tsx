"use client";

import { useCallback, useEffect, useState } from "react";

import { ChannelStrip, Empty, ErrorNote, Panel, PlatformDot, Spinner, Status } from "@/components/ui";
import { api, platformLabel, qs } from "@/lib/api";
import { daysAgo, timeAgo } from "@/lib/format";
import type {
  Account,
  Connection,
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
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [coverage, setCoverage] = useState<Record<string, Set<string>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, c, a, s] = await Promise.all([
        api.get<{ platforms: PlatformInfo[] }>("/platforms"),
        api.get<Connection[]>("/connections"),
        api.get<Account[]>("/accounts"),
        api.get<Schedule[]>("/schedules"),
      ]);
      setPlatforms(p.platforms);
      setConnections(c);
      setAccounts(a);
      setSchedules(s);

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
      setError(e instanceof Error ? e.message : "Could not load your connections.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Spinner label="Loading connections" />;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Connections</h1>
          <p className="mt-0.5 text-sm text-muted">
            Each platform is a tributary. A gap in a channel means data didn&apos;t
            arrive that day.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setAdding((v) => !v)}>
          {adding ? "Close" : "Add connection"}
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
          <Empty
            title="No connections yet."
            hint="Add a platform to start pulling its numbers into majra."
          />
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
              onChange={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ConnectionCard({
  connection,
  accounts,
  schedule,
  days,
  onChange,
}: {
  connection: Connection;
  accounts: Account[];
  schedule: Schedule | null;
  days: Set<string>;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const isConfig = CONFIG.has(connection.platform);

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
      setNote("Sync started — watch it on the Sync jobs page.");
      onChange();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not start that sync.");
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
      setNote(e instanceof Error ? e.message : "Could not create that schedule.");
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
            {platformLabel(connection.platform)}
            <Status value={connection.status} />
          </h3>
          <p className="mt-1 text-sm text-muted">
            {accounts.length > 0
              ? accounts.map((a) => a.name).join(" · ")
              : "No accounts discovered yet — run a sync."}
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
          {busy ? "Working…" : "Sync now"}
        </button>

        {schedule ? (
          <button className="btn-quiet" onClick={toggleSchedule} disabled={busy}>
            {schedule.enabled ? "Pause daily sync" : "Resume daily sync"}
          </button>
        ) : (
          <button className="btn-quiet" onClick={createSchedule} disabled={busy}>
            Sync daily
          </button>
        )}

        <span className="ms-auto text-sm text-muted">
          {schedule
            ? `${schedule.enabled ? "Daily" : "Paused"} · last run ${timeAgo(schedule.last_enqueued_at)}`
            : "Manual only"}
        </span>
      </div>

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
  const [chosen, setChosen] = useState<PlatformInfo | null>(null);
  const [saJson, setSaJson] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      setError(e instanceof Error ? e.message : "Could not start that connection.");
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
      if (e instanceof SyntaxError) setError("That isn't valid JSON. Paste the whole key file.");
      else setError(e instanceof Error ? e.message : "Could not save that connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Add a connection">
      {error && <ErrorNote message={error} />}

      <div className="mt-1 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {platforms.map((p) => (
          <button
            key={p.platform}
            onClick={() =>
              p.auth_kind === "oauth" ? startOAuth(p) : setChosen(p)
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
              <span className="block truncate">{platformLabel(p.platform)}</span>
              <span className="block text-eyebrow text-muted">
                {connected.has(p.platform) ? "Connected" : p.auth_kind === "oauth" ? "Authorize" : "Service account"}
              </span>
            </span>
          </button>
        ))}
      </div>

      {chosen?.auth_kind === "service_account" && (
        <div className="mt-4 flex flex-col gap-3 border-t border-line pt-4">
          <p className="text-sm text-muted">
            {platformLabel(chosen.platform)} reads through a Google service account.
            Grant it access in Google, then paste its key file here.
          </p>
          <div>
            <label htmlFor="sa" className="eyebrow mb-1.5 block">
              Service account key (JSON)
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
                GA4 property ID
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
            {busy ? "Saving…" : "Save connection"}
          </button>
        </div>
      )}
    </Panel>
  );
}
