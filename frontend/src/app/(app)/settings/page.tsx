"use client";

import { useCallback, useEffect, useState } from "react";

import { Empty, ErrorNote, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Account, ApiKey } from "@/lib/types";

type Me = { id: number; email: string; created_at: string };

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);

  const [keyName, setKeyName] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [m, a, k] = await Promise.all([
      api.get<Me>("/auth/me"),
      api.get<Account[]>("/accounts"),
      api.get<ApiKey[]>("/api-keys"),
    ]);
    setMe(m);
    setAccounts(a);
    setKeys(k);
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
      setError(err instanceof Error ? err.message : "Could not create that key.");
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
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      </header>

      <Panel title="Account">
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-muted">Email</dt>
          <dd>{me?.email}</dd>
          <dt className="text-muted">Joined</dt>
          <dd className="num">{me?.created_at?.slice(0, 10)}</dd>
        </dl>
      </Panel>

      <Panel title={`API keys (${live.length})`}>
        <p className="mb-4 text-sm text-muted">
          A key reads your numbers from your own scripts — it can call{" "}
          <code className="num">/metrics</code> and <code className="num">/exports</code>,
          and nothing else. It can&apos;t start a sync, add a connection, or make
          another key.
        </p>

        {freshKey && (
          <div className="mb-4 rounded-md border border-accent/40 bg-accent/10 p-3">
            <p className="text-sm">
              Copy this key now — majra stores only its hash, so it can&apos;t be
              shown again.
            </p>
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
                {copied ? "Copied" : "Copy"}
              </button>
              <button className="btn-quiet" onClick={() => setFreshKey(null)}>
                Done
              </button>
            </div>
          </div>
        )}

        {error && <ErrorNote message={error} />}

        <form onSubmit={createKey} className="mb-4 flex items-end gap-2">
          <div className="flex-1">
            <label htmlFor="keyname" className="eyebrow mb-1.5 block">
              What is this key for?
            </label>
            <input
              id="keyname"
              required
              className="field"
              placeholder="Nightly spreadsheet pull"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
            />
          </div>
          <button className="btn-primary" disabled={busy || !keyName}>
            {busy ? "Creating…" : "Create key"}
          </button>
        </form>

        {live.length === 0 ? (
          <Empty title="No keys yet." />
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
                  {k.last_used_at ? `used ${timeAgo(k.last_used_at)}` : "never used"}
                </span>
                <button
                  className="text-muted hover:text-bad"
                  onClick={() => revoke(k.id)}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title={`Accounts majra can see (${accounts.length})`}>
        {accounts.length === 0 ? (
          <p className="text-sm text-muted">
            None yet. Connect a platform and run a sync.
          </p>
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
    </div>
  );
}
