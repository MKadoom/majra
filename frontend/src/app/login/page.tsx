"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorNote } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
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
      setError(body.error ?? "That didn't work. Check your details and try again.");
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
          <p className="mt-1 text-sm text-muted">
            Every platform&apos;s numbers, in one channel.
          </p>
        </header>

        <form onSubmit={submit} className="panel flex flex-col gap-4 p-5">
          <div>
            <label htmlFor="email" className="eyebrow mb-1.5 block">
              Email
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
              Password
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
              <p className="mt-1.5 text-eyebrow text-muted">At least 8 characters.</p>
            )}
          </div>

          {error && <ErrorNote message={error} />}

          <button type="submit" className="btn-primary" disabled={busy}>
            {busy
              ? "Working…"
              : mode === "login"
                ? "Sign in"
                : "Create account"}
          </button>

          <button
            type="button"
            className="text-sm text-muted hover:text-ink"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
          >
            {mode === "login"
              ? "No account yet? Create one"
              : "Already have an account? Sign in"}
          </button>
        </form>
      </div>
    </main>
  );
}
