import { NextRequest, NextResponse } from "next/server";

import { LOCALES, Locale } from "@/lib/i18n";

export const LOCALE_COOKIE = "majra_locale";

/** Persist the chosen language. Read server-side in the root layout, which is
 *  what sets `dir` and `lang` on <html> — so the whole layout flips. */
export async function POST(req: NextRequest) {
  const { locale } = (await req.json()) as { locale: Locale };
  if (!LOCALES.includes(locale)) {
    return NextResponse.json({ error: "unknown locale" }, { status: 422 });
  }
  const res = NextResponse.json({ ok: true, locale });
  res.cookies.set(LOCALE_COOKIE, locale, {
    httpOnly: false, // not a secret; a preference
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });
  return res;
}
