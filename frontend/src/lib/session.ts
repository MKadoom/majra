import type { NextResponse } from "next/server";

/**
 * The browser never sees a token. Both the access and refresh tokens live in
 * httpOnly cookies; every backend call goes through /api/proxy, which attaches
 * the access token server-side and silently refreshes it on 401. That keeps
 * tokens out of client-side JavaScript and means the backend needs no CORS.
 */
export const API_URL = process.env.MAJRA_API_URL ?? "http://localhost:8000";

export const ACCESS_COOKIE = "majra_at";
export const REFRESH_COOKIE = "majra_rt";

const BASE_COOKIE = {
  httpOnly: true,
  sameSite: "lax" as const,
  path: "/",
  secure: process.env.NODE_ENV === "production",
};

export type TokenPair = { access_token: string; refresh_token: string };

export function writeSession(res: NextResponse, tokens: TokenPair) {
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, {
    ...BASE_COOKIE,
    maxAge: 60 * 15, // matches the backend's 15-minute access token
  });
  res.cookies.set(REFRESH_COOKIE, tokens.refresh_token, {
    ...BASE_COOKIE,
    maxAge: 60 * 60 * 24 * 30, // 30-day refresh token
  });
}

export function clearSession(res: NextResponse) {
  res.cookies.set(ACCESS_COOKIE, "", { ...BASE_COOKIE, maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { ...BASE_COOKIE, maxAge: 0 });
}

/** Exchange a refresh token for a fresh pair. Returns null if it's no longer valid. */
export async function refreshTokens(refreshToken: string): Promise<TokenPair | null> {
  const res = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!res.ok) return null;
  return (await res.json()) as TokenPair;
}
