import type { Metadata } from "next";
import { cookies } from "next/headers";

import { LOCALE_COOKIE } from "@/app/api/locale/route";
import { DEFAULT_LOCALE, DIR, LOCALES, Locale } from "@/lib/i18n";
import { LocaleProvider } from "@/lib/locale";

import "./globals.css";

export const metadata: Metadata = {
  title: "majra · مجرى",
  description: "Your marketing data, in one channel. · أرقام كل المنصات، في مجرى واحد.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const cookie = cookies().get(LOCALE_COOKIE)?.value as Locale | undefined;
  const locale: Locale =
    cookie && LOCALES.includes(cookie) ? cookie : DEFAULT_LOCALE;

  // `dir` is set here, on <html>. Every component uses logical properties
  // (border-inline-end, margin-inline-start, text-start) rather than left/right,
  // so this one attribute mirrors the entire interface.
  return (
    <html lang={locale} dir={DIR[locale]}>
      <body>
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
