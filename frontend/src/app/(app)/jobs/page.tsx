"use client";

import { Fragment, useCallback, useEffect, useState } from "react";

import { Empty, ErrorNote, Panel, PlatformDot, Spinner, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { Connection, SyncJob, SyncLog } from "@/lib/types";

export default function JobsPage() {
  const { t, p: pName, ago, num } = useLocale();
  const [jobs, setJobs] = useState<SyncJob[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [logs, setLogs] = useState<Record<number, SyncLog[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [j, c] = await Promise.all([
        api.get<SyncJob[]>("/syncs?page_size=50"),
        api.get<Connection[]>("/connections"),
      ]);
      setJobs(j);
      setConnections(c);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("jobs.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
    // A running job is worth watching; poll gently while the tab is open.
    const t = setInterval(() => void load(), 10_000);
    return () => clearInterval(t);
  }, [load]);

  async function toggle(job: SyncJob) {
    if (open === job.id) {
      setOpen(null);
      return;
    }
    setOpen(job.id);
    if (!logs[job.id]) {
      const rows = await api.get<SyncLog[]>(`/logs?sync_job_id=${job.id}`);
      setLogs((prev) => ({ ...prev, [job.id]: rows }));
    }
  }

  const platformOf = (id: number) =>
    connections.find((c) => c.id === id)?.platform ?? "unknown";

  if (loading) return <Spinner label={t("common.loading")} />;

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">{t("jobs.title")}</h1>
        <p className="mt-0.5 text-sm text-muted">{t("jobs.subtitle")}</p>
      </header>

      {error && <ErrorNote message={error} />}

      <Panel>
        {jobs.length === 0 ? (
          <Empty title={t("jobs.none")} hint={t("jobs.noneHint")} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line">
                  <th className="px-2 py-2 text-start eyebrow">{t("g.platform")}</th>
                  <th className="px-2 py-2 text-start eyebrow">{t("jobs.range")}</th>
                  <th className="px-2 py-2 text-start eyebrow">{t("jobs.kind")}</th>
                  <th className="px-2 py-2 text-start eyebrow">{t("jobs.status")}</th>
                  <th className="px-2 py-2 text-end eyebrow">{t("jobs.rows")}</th>
                  <th className="px-2 py-2 text-end eyebrow">{t("jobs.when")}</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <Fragment key={job.id}>
                    <tr
                      onClick={() => toggle(job)}
                      className="cursor-pointer border-b border-line/60 hover:bg-raised"
                    >
                      <td className="px-2 py-2">
                        <span className="flex items-center gap-1.5">
                          <PlatformDot platform={platformOf(job.connection_id)} />
                          {pName(platformOf(job.connection_id))}
                        </span>
                      </td>
                      <td className="num px-2 py-2 text-muted">
                        {job.date_from} → {job.date_to}
                      </td>
                      <td className="px-2 py-2 text-muted">{job.kind}</td>
                      <td className="px-2 py-2">
                        <Status value={job.status} />
                      </td>
                      <td className="num px-2 py-2 text-end">
                        {job.stats?.rows_inserted != null
                          ? num(job.stats.rows_inserted)
                          : "—"}
                      </td>
                      <td className="px-2 py-2 text-end text-muted">
                        {ago(job.finished_at ?? job.created_at)}
                      </td>
                    </tr>

                    {open === job.id && (
                      <tr className="border-b border-line/60">
                        <td colSpan={6} className="bg-ground/60 px-4 py-3">
                          {job.error && <ErrorNote message={job.error} />}
                          {job.stats && (
                            <p className="mb-2 text-sm text-muted">
                              {t("jobs.stats", {
                                accounts: num(job.stats.accounts ?? 0),
                                fetched: num(job.stats.rows_fetched ?? 0),
                                deleted: num(job.stats.rows_deleted ?? 0),
                                inserted: num(job.stats.rows_inserted ?? 0),
                              })}
                            </p>
                          )}
                          {!logs[job.id] ? (
                            <p className="text-sm text-muted">{t("jobs.loadingSteps")}</p>
                          ) : logs[job.id].length === 0 ? (
                            <p className="text-sm text-muted">{t("jobs.noSteps")}</p>
                          ) : (
                            <ol className="flex flex-col gap-1">
                              {logs[job.id].map((l, i) => (
                                <li key={i} className="num text-xs text-muted">
                                  <span className="text-ink">{l.event}</span>{" "}
                                  {Object.entries(l.data)
                                    .map(([k, v]) => `${k}=${String(v)}`)
                                    .join(" ")}
                                </li>
                              ))}
                            </ol>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
