"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useLocale } from "@/lib/locale";

const LINKS = [
  { href: "/explore", key: "nav.explore" },
  { href: "/connections", key: "nav.connections" },
  { href: "/jobs", key: "nav.jobs" },
  { href: "/reports", key: "nav.reports" },
  { href: "/settings", key: "nav.settings" },
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
    <nav className="flex w-56 shrink-0 flex-col border-e border-line bg-surface p-4">
      <Link href="/explore" className="mb-6 block">
        <span className="text-lg font-semibold tracking-tight">majra</span>
        <span className="ms-2 text-muted" lang="ar" dir="rtl">
          مجرى
        </span>
        <p className="mt-0.5 text-eyebrow text-muted">{t("app.tagline")}</p>
      </Link>

      <ul className="flex flex-col gap-1">
        {LINKS.map((l) => {
          const active = pathname.startsWith(l.href);
          return (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`block rounded-md border-s-2 px-3 py-2 text-sm transition-colors ${
                  active
                    ? "border-accent bg-raised text-ink"
                    : "border-transparent text-muted hover:bg-raised hover:text-ink"
                }`}
              >
                {t(l.key)}
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="mt-auto border-t border-line pt-4">
        {email && <p className="truncate text-sm text-muted">{email}</p>}
        <div className="mt-2 flex items-center gap-3">
          <button onClick={signOut} className="text-sm text-muted hover:text-ink">
            {t("nav.signOut")}
          </button>
          <button
            onClick={switchLocale}
            lang={locale === "en" ? "ar" : "en"}
            className="ms-auto text-sm text-muted hover:text-ink"
          >
            {t("nav.language")}
          </button>
        </div>
      </div>
    </nav>
  );
}
