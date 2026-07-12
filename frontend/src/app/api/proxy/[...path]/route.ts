import { NextRequest, NextResponse } from "next/server";

import {
  ACCESS_COOKIE,
  API_URL,
  REFRESH_COOKIE,
  clearSession,
  refreshTokens,
  writeSession,
} from "@/lib/session";

/**
 * Every backend call from the browser lands here. This handler attaches the
 * access token from the httpOnly cookie; if the backend says it expired, it
 * refreshes once, retries, and rewrites the cookies — all server-side, so the
 * page never has to think about auth and no token reaches client JavaScript.
 */
async function proxy(req: NextRequest, path: string[]) {
  const url = `${API_URL}/${path.join("/")}${req.nextUrl.search}`;
  const body =
    req.method === "GET" || req.method === "DELETE" ? undefined : await req.text();

  const call = (token: string | undefined) =>
    fetch(url, {
      method: req.method,
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body,
      cache: "no-store",
    });

  let upstream = await call(req.cookies.get(ACCESS_COOKIE)?.value);
  let refreshed = null;

  if (upstream.status === 401) {
    const refreshToken = req.cookies.get(REFRESH_COOKIE)?.value;
    refreshed = refreshToken ? await refreshTokens(refreshToken) : null;
    if (!refreshed) {
      // The session is genuinely over — clear it so the UI can send them to sign in.
      const res = NextResponse.json({ error: "Session expired" }, { status: 401 });
      clearSession(res);
      return res;
    }
    upstream = await call(refreshed.access_token);
  }

  const text = await upstream.text();
  const res = new NextResponse(text, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
  if (refreshed) writeSession(res, refreshed);
  return res;
}

type Ctx = { params: { path: string[] } };

export const GET = (req: NextRequest, { params }: Ctx) => proxy(req, params.path);
export const POST = (req: NextRequest, { params }: Ctx) => proxy(req, params.path);
export const PATCH = (req: NextRequest, { params }: Ctx) => proxy(req, params.path);
export const DELETE = (req: NextRequest, { params }: Ctx) => proxy(req, params.path);
