"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorNote } from "@/components/ui";
import { useLocale } from "@/lib/locale";

export default function LoginPage() {
  const router = useRouter();
  const { t } = useLocale();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/auth", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode, email, password }),
    });
    setBusy(false);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? t("auth.failed"));
      return;
    }
    router.push("/explore");
    router.refresh();
  }

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="w-full max-w-sm">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">
            majra{" "}
            <span className="text-muted" lang="ar" dir="rtl">
              مجرى
            </span>
          </h1>
          <p className="mt-1 text-sm text-muted">{t("app.subtitle")}</p>
        </header>

        <form onSubmit={submit} className="panel flex flex-col gap-4 p-5">
          <div>
            <label htmlFor="email" className="eyebrow mb-1.5 block">
              {t("auth.email")}
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              className="field"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className="eyebrow mb-1.5 block">
              {t("auth.password")}
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              className="field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {mode === "register" && (
              <p className="mt-1.5 text-eyebrow text-muted">{t("auth.minChars")}</p>
            )}
          </div>

          {error && <ErrorNote message={error} />}

          <button type="submit" className="btn-primary" disabled={busy}>
            {busy
              ? t("common.working")
              : mode === "login"
                ? t("auth.signIn")
                : t("auth.createAccount")}
          </button>

          <button
            type="button"
            className="text-sm text-muted hover:text-ink"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
          >
            {mode === "login" ? t("auth.toRegister") : t("auth.toLogin")}
          </button>
        </form>
      </div>
    </main>
  );
}
