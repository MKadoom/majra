import { NextRequest, NextResponse } from "next/server";

import { API_URL, TokenPair, clearSession, writeSession } from "@/lib/session";

/** Sign in or create an account, then store the session in httpOnly cookies. */
export async function POST(req: NextRequest) {
  const { mode, email, password } = (await req.json()) as {
    mode: "login" | "register";
    email: string;
    password: string;
  };

  const upstream = await fetch(
    `${API_URL}/auth/${mode === "register" ? "register" : "login"}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    },
  );

  if (!upstream.ok) {
    const detail = await upstream
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    return NextResponse.json(
      { error: typeof detail === "string" ? detail : "Sign in failed" },
      { status: upstream.status },
    );
  }

  const tokens = (await upstream.json()) as TokenPair;
  const res = NextResponse.json({ ok: true });
  writeSession(res, tokens);
  return res;
}

/** Sign out: drop the cookies. */
export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  clearSession(res);
  return res;
}
