"use client";

import {
  Compass,
  FileText,
  Languages,
  LogOut,
  Plug,
  RefreshCw,
  Settings,
  Target,
  Wallet,
  Waves,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useLocale } from "@/lib/locale";

const LINKS: { href: string; key: string; icon: LucideIcon }[] = [
  { href: "/explore", key: "nav.explore", icon: Compass },
  { href: "/funding", key: "nav.funding", icon: Wallet },
  { href: "/budgets", key: "nav.budgets", icon: Target },
  { href: "/connections", key: "nav.connections", icon: Plug },
  { href: "/jobs", key: "nav.jobs", icon: RefreshCw },
  { href: "/reports", key: "nav.reports", icon: FileText },
  { href: "/settings", key: "nav.settings", icon: Settings },
];

export function Nav({ email }: { email: string | null }) {
  const pathname = usePathname();
  const router = useRouter();
  const { t, locale, switchLocale } = useLocale();

  async function signOut() {
    await fetch("/api/auth", { method: "DELETE" });
    router.push("/login");
  }

  return (
    // border-inline-end (border-e), never border-r: setting dir="rtl" on <html>
    // mirrors this rail to the other side of the screen without a single change.
    <nav className="flex w-60 shrink-0 flex-col border-e border-line bg-surface/60 p-4 backdrop-blur">
      <Link href="/explore" className="mb-7 flex items-center gap-2.5 px-2">
        <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-gradient-to-b from-accent to-accent-strong text-ground shadow-glow">
          <Waves className="size-4" />
        </span>
        <span className="min-w-0">
          <span className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight text-ink">majra</span>
            <span className="text-sm text-muted" lang="ar" dir="rtl">
              مجرى
            </span>
          </span>
          <span className="block text-eyebrow text-muted">{t("app.tagline")}</span>
        </span>
      </Link>

      <ul className="flex flex-col gap-1">
        {LINKS.map((l) => {
          const active = pathname.startsWith(l.href);
          const Icon = l.icon;
          return (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`group flex items-center gap-3 rounded-md border-s-2 px-3 py-2 text-sm transition-all ${
                  active
                    ? "border-accent bg-accent/10 text-ink shadow-[inset_0_0_0_1px_rgba(34,211,238,0.08)]"
                    : "border-transparent text-muted hover:bg-raised hover:text-ink"
                }`}
              >
                <Icon
                  className={`size-4 shrink-0 transition-colors ${
                    active ? "text-accent" : "text-muted group-hover:text-ink"
                  }`}
                />
                {t(l.key)}
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="mt-auto border-t border-line pt-4">
        {email && <p className="truncate px-2 text-sm text-muted">{email}</p>}
        <div className="mt-2 flex items-center gap-1">
          <button
            onClick={signOut}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
          >
            <LogOut className="size-4" />
            {t("nav.signOut")}
          </button>
          <button
            onClick={switchLocale}
            lang={locale === "en" ? "ar" : "en"}
            className="ms-auto flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
          >
            <Languages className="size-4" />
            {t("nav.language")}
          </button>
        </div>
      </div>
    </nav>
  );
}
