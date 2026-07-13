"use client";

import { useRouter } from "next/navigation";
import { ReactNode, createContext, useCallback, useContext, useMemo } from "react";

import {
  DIR,
  Locale,
  Vars,
  formatDate,
  formatMeasure,
  formatNumber,
  groupLabel,
  measureLabel,
  platformName,
  statusLabel,
  timeAgo,
  translate,
} from "@/lib/i18n";

type Ctx = {
  locale: Locale;
  dir: "ltr" | "rtl";
  t: (key: string, vars?: Vars) => string;
  m: (key: string) => string; // measure label
  g: (key: string) => string; // group-key label
  p: (key: string) => string; // platform name
  s: (key: string) => string; // status label
  num: (value: number, opts?: Intl.NumberFormatOptions) => string;
  measure: (key: string, value: unknown) => string;
  date: (iso: string) => string;
  ago: (iso: string | null) => string;
  switchLocale: () => void;
};

const LocaleContext = createContext<Ctx | null>(null);

export function LocaleProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: ReactNode;
}) {
  const router = useRouter();

  const switchLocale = useCallback(async () => {
    const next: Locale = locale === "en" ? "ar" : "en";
    await fetch("/api/locale", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ locale: next }),
    });
    // The cookie decides `dir` and `lang` on <html>, which is rendered on the
    // server — so a refresh is what actually flips the layout.
    router.refresh();
  }, [locale, router]);

  const value = useMemo<Ctx>(
    () => ({
      locale,
      dir: DIR[locale],
      t: (key, vars) => translate(locale, key, vars),
      m: (key) => measureLabel(locale, key),
      g: (key) => groupLabel(locale, key),
      p: (key) => platformName(locale, key),
      s: (key) => statusLabel(locale, key),
      num: (value, opts) => formatNumber(locale, value, opts),
      measure: (key, value) => formatMeasure(locale, key, value),
      date: (iso) => formatDate(locale, iso),
      ago: (iso) => timeAgo(locale, iso),
      switchLocale,
    }),
    [locale, switchLocale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): Ctx {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("useLocale must be used inside <LocaleProvider>");
  return ctx;
}
