import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "majra",
  description: "Your marketing data, in one channel.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // dir is set here so switching to an Arabic locale later flips the whole
  // layout — every component uses logical properties, never left/right.
  return (
    <html lang="en" dir="ltr">
      <body>{children}</body>
    </html>
  );
}
