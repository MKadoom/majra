"use client";

import { Loader2 } from "lucide-react";
import { ReactNode } from "react";

import { platformColor } from "@/lib/api";
import { dateRange } from "@/lib/format";
import { useLocale } from "@/lib/locale";

import { Alert } from "./alert";
import { Badge, type BadgeProps } from "./badge";

// Re-export the shadcn primitives so pages can `import { Button, Card } from
// "@/components/ui"` or reach for the individual files directly.
export * from "./button";
export * from "./card";
export * from "./input";
export * from "./textarea";
export * from "./label";
export * from "./badge";
export * from "./table";
export * from "./separator";
export * from "./skeleton";
export * from "./alert";
export * from "./select";
export * from "./dialog";
export * from "./page-header";

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
    <section className={`panel animate-fade-in ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          {title && <h2 className="eyebrow">{title}</h2>}
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

// status value → Badge variant
const STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
  success: "good",
  active: "good",
  running: "accent",
  pending: "default",
  failed: "bad",
  needs_reauth: "warn",
};

export function Status({ value }: { value: string }) {
  const { s, locale } = useLocale();
  const label = s(value);
  return (
    <Badge
      variant={STATUS_VARIANT[value] ?? "default"}
      className={locale === "en" ? "uppercase" : ""}
    >
      {label.startsWith("status.") ? value.replace(/_/g, " ") : label}
    </Badge>
  );
}

export function PlatformDot({ platform }: { platform: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-2 w-2 shrink-0 rounded-full ring-1 ring-inset ring-white/10"
      style={{ background: platformColor(platform) }}
    />
  );
}

/**
 * The channel strip — majra's signature. One tick per day for the window, lit
 * where data landed and dark where it didn't, so a gap in a tributary is visible
 * at a glance.
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
      <div
        className="flex items-end gap-[3px]"
        role="img"
        aria-label={`${all.length - gaps} / ${all.length}`}
      >
        {all.map((d) => {
          const on = days.has(d);
          return (
            <span
              key={d}
              title={`${d} — ${on ? "data" : "no data"}`}
              className="h-5 w-[6px] rounded-[2px] transition-shadow"
              style={{
                background: on ? hue : "#1E2732",
                boxShadow: on ? `0 0 8px -2px ${hue}` : "none",
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
    <div className="py-12 text-center">
      <p className="text-sm text-ink">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-2 py-10 text-sm text-muted"
      role="status"
    >
      <Loader2 className="size-4 animate-spin text-accent" />
      {label}…
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return <Alert variant="bad">{message}</Alert>;
}
