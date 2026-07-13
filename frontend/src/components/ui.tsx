"use client";

import { ReactNode } from "react";

import { platformColor } from "@/lib/api";
import { dateRange } from "@/lib/format";
import { useLocale } from "@/lib/locale";

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          {title && <h2 className="eyebrow">{title}</h2>}
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const STATUS_STYLE: Record<string, string> = {
  success: "text-good border-good/40 bg-good/10",
  active: "text-good border-good/40 bg-good/10",
  running: "text-accent border-accent/40 bg-accent/10",
  pending: "text-muted border-line bg-raised",
  failed: "text-bad border-bad/40 bg-bad/10",
  needs_reauth: "text-warn border-warn/40 bg-warn/10",
};

export function Status({ value }: { value: string }) {
  const { s, locale } = useLocale();
  const label = s(value);
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-eyebrow ${
        // Uppercasing is a Latin typographic device; Arabic has no case, and
        // forcing it just adds noise.
        locale === "en" ? "uppercase" : ""
      } ${STATUS_STYLE[value] ?? "border-line bg-raised text-muted"}`}
    >
      {label.startsWith("status.") ? value.replace(/_/g, " ") : label}
    </span>
  );
}

export function PlatformDot({ platform }: { platform: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-2 w-2 shrink-0 rounded-full"
      style={{ background: platformColor(platform) }}
    />
  );
}

/**
 * The channel strip — majra's signature.
 *
 * One tick per day for the window. A tick is lit where data landed and dark
 * where it didn't, so a gap in a tributary is visible at a glance: a broken
 * connector reads as a hole in the channel, without anyone opening a log.
 */
export function ChannelStrip({
  platform,
  days,
  from,
  to,
}: {
  platform: string;
  days: Set<string>;
  from: string;
  to: string;
}) {
  const { t } = useLocale();
  const all = dateRange(from, to);
  const hue = platformColor(platform);
  const gaps = all.filter((d) => !days.has(d)).length;
  return (
    <div>
      {/* The strip reads left-to-right in English and right-to-left in Arabic:
          flex direction follows `dir`, so the oldest day is always at the start. */}
      <div className="flex items-end gap-[3px]" role="img"
           aria-label={`${all.length - gaps} / ${all.length}`}>
        {all.map((d) => {
          const on = days.has(d);
          return (
            <span
              key={d}
              title={`${d} — ${on ? "data" : "no data"}`}
              className="h-5 w-[6px] rounded-[2px]"
              style={{
                background: on ? hue : "#232C36",
                opacity: on ? 1 : 1,
              }}
            />
          );
        })}
      </div>
      <p className="mt-1.5 text-eyebrow text-muted">
        {gaps === 0
          ? t("conn.daysNoGaps", { n: all.length })
          : gaps === 1
            ? t("conn.dayMissing")
            : t("conn.daysMissing", { n: gaps })}
      </p>
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="py-10 text-center">
      <p className="text-sm text-ink">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <p className="py-8 text-center text-sm text-muted" role="status">
      {label}…
    </p>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
      {message}
    </p>
  );
}
