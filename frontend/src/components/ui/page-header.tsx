import { type LucideIcon } from "lucide-react";
import { type ReactNode } from "react";

// A consistent page top: an accent icon tile, title + optional subtitle, and an
// optional slot on the trailing edge for controls/actions. RTL-safe via flex +
// logical spacing.
export function PageHeader({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: LucideIcon;
  title: string;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg border border-line bg-raised text-accent shadow-card">
          <Icon className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
        </div>
      </div>
      {children && <div className="flex flex-wrap items-end gap-2">{children}</div>}
    </header>
  );
}
