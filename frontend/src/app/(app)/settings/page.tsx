"use client";

import { useEffect, useState } from "react";

import { Panel, PlatformDot, Spinner } from "@/components/ui";
import { api, platformLabel } from "@/lib/api";
import type { Account } from "@/lib/types";

type Me = { id: number; email: string; created_at: string };

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.get<Me>("/auth/me"), api.get<Account[]>("/accounts")])
      .then(([m, a]) => {
        setMe(m);
        setAccounts(a);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

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

      <Panel title="API keys">
        <p className="text-sm text-muted">
          Read-only keys for pulling <code className="num">/metrics</code> from your own
          scripts are coming next, alongside scheduled exports to CSV and Google Sheets.
        </p>
      </Panel>
    </div>
  );
}
