"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Nav } from "@/components/nav";
import { api } from "@/lib/api";
import { useLocale } from "@/lib/locale";

type Me = { id: number; email: string };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { t } = useLocale();
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // The proxy refreshes a stale access token transparently; a 401 here means
    // the refresh token is gone too, so the session is genuinely over.
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch(() => router.replace("/login"))
      .finally(() => setChecked(true));
  }, [router]);

  if (!checked) {
    return (
      <main className="grid min-h-screen place-items-center">
        <p className="text-sm text-muted">{t("auth.checking")}</p>
      </main>
    );
  }
  if (!me) return null;

  return (
    <div className="flex min-h-screen">
      <Nav email={me.email} />
      <main className="min-w-0 flex-1 p-6">{children}</main>
    </div>
  );
}
