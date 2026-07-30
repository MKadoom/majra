"use client";

import { useCallback, useEffect, useState } from "react";

import { Target } from "lucide-react";

import { Empty, ErrorNote, PageHeader, Panel, PlatformDot, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { Account, Budget, Pacing } from "@/lib/types";

const money = (n: number, ccy: string) =>
  `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${ccy}`;

const STATUS_STYLE: Record<string, string> = {
  over: "text-bad border-bad/40 bg-bad/10",
  under: "text-muted border-line bg-raised",
  on_track: "text-good border-good/40 bg-good/10",
};

export default function BudgetsPage() {
  const { t } = useLocale();
  const [pacing, setPacing] = useState<Pacing[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, b, a] = await Promise.all([
        api.get<Pacing[]>("/pacing"),
        api.get<Budget[]>("/budgets"),
        api.get<Account[]>("/accounts"),
      ]);
      setPacing(p);
      setBudgets(b);
      setAccounts(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Spinner label={t("common.loading")} />;

  const paceById = new Map(pacing.map((p) => [p.budget_id, p]));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={Target}
        title={t("budget.title")}
        subtitle={t("budget.subtitle")}
      />

      {error && <ErrorNote message={error} />}

      {budgets.length === 0 ? (
        <Panel>
          <Empty title={t("budget.none")} hint={t("budget.noneHint")} />
        </Panel>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {budgets.map((b) => {
            const p = paceById.get(b.id);
            const pct = p?.pct_used != null ? Math.min(p.pct_used, 1.5) : 0;
            return (
              <article key={b.id} className="panel p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-medium">{b.name}</h3>
                    <p className="mt-0.5 text-eyebrow text-muted">
                      {b.platform ? (
                        <span className="inline-flex items-center gap-1">
                          <PlatformDot platform={b.platform} /> {b.platform}
                        </span>
                      ) : (
                        t("budget.allPlatforms")
                      )}
                    </p>
                  </div>
                  {p && (
                    <span
                      className={`rounded-full border px-2 py-0.5 text-eyebrow ${STATUS_STYLE[p.status]}`}
                    >
                      {t(`budget.status.${p.status}`)}
                    </span>
                  )}
                </div>

                {p && (
                  <>
                    <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-raised">
                      <div
                        className={`h-full ${p.status === "over" ? "bg-bad" : "bg-accent"}`}
                        style={{ inlineSize: `${Math.round(pct * 100)}%` }}
                      />
                    </div>
                    <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
                      <div>
                        <dt className="text-eyebrow text-muted">{t("budget.spent")}</dt>
                        <dd className="num">{money(p.spent_mtd, p.currency)}</dd>
                      </div>
                      <div>
                        <dt className="text-eyebrow text-muted">{t("budget.projected")}</dt>
                        <dd className="num">{money(p.projected_month_end, p.currency)}</dd>
                      </div>
                      <div>
                        <dt className="text-eyebrow text-muted">{t("budget.monthly")}</dt>
                        <dd className="num">{money(p.amount_monthly, p.currency)}</dd>
                      </div>
                    </dl>
                  </>
                )}

                <div className="mt-3 border-t border-line pt-2">
                  <button
                    className="text-sm text-muted hover:text-danger"
                    onClick={async () => {
                      await api.del(`/budgets/${b.id}`);
                      void load();
                    }}
                  >
                    {t("common.delete")}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <NewBudget accounts={accounts} onDone={load} />
    </div>
  );
}

function NewBudget({
  accounts,
  onDone,
}: {
  accounts: Account[];
  onDone: () => void;
}) {
  const { t } = useLocale();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const acc = accountId ? accounts.find((a) => String(a.id) === accountId) : null;
      await api.post("/budgets", {
        name,
        amount_monthly: Number(amount),
        currency: currency.toUpperCase(),
        platform: acc ? acc.platform : null,
        account_id: acc ? acc.id : null,
      });
      setName("");
      setAmount("");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t("budget.addTitle")}>
      {error && <ErrorNote message={error} />}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <input
          className="field"
          placeholder={t("budget.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="field num"
          type="number"
          placeholder={t("budget.monthly")}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        <input
          className="field"
          placeholder="USD"
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
        />
        <select
          className="field"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
        >
          <option value="">{t("budget.allAccounts")}</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </div>
      <button
        className="btn-primary mt-3 self-start"
        onClick={save}
        disabled={busy || !name || !amount}
      >
        {busy ? t("common.working") : t("budget.add")}
      </button>
    </Panel>
  );
}
