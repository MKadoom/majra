"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const LINKS = [
  { href: "/explore", label: "Explore" },
  { href: "/connections", label: "Connections" },
  { href: "/jobs", label: "Sync jobs" },
  { href: "/reports", label: "Reports" },
  { href: "/settings", label: "Settings" },
];

export function Nav({ email }: { email: string | null }) {
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    await fetch("/api/auth", { method: "DELETE" });
    router.push("/login");
  }

  return (
    // border-inline-end, not border-r: an Arabic locale flips this for free.
    <nav className="flex w-56 shrink-0 flex-col border-e border-line bg-surface p-4">
      <Link href="/explore" className="mb-6 block">
        <span className="text-lg font-semibold tracking-tight">majra</span>
        <span className="ms-2 text-muted" lang="ar" dir="rtl">
          مجرى
        </span>
        <p className="mt-0.5 text-eyebrow text-muted">One channel</p>
      </Link>

      <ul className="flex flex-col gap-1">
        {LINKS.map((l) => {
          const active = pathname.startsWith(l.href);
          return (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`block rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-raised text-ink shadow-[inset_2px_0_0_0_var(--tw-shadow-color)] shadow-accent"
                    : "text-muted hover:bg-raised hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="mt-auto border-t border-line pt-4">
        {email && <p className="truncate text-sm text-muted">{email}</p>}
        <button onClick={signOut} className="mt-2 text-sm text-muted hover:text-ink">
          Sign out
        </button>
      </div>
    </nav>
  );
}
