import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms of Service · شروط الاستخدام · majra",
  description: "Terms for using majra.",
};

const UPDATED = "2026-07-16";

export default function TermsPage() {
  return (
    <main
      style={{
        maxWidth: 760,
        margin: "0 auto",
        padding: "48px 24px 96px",
        fontFamily: "system-ui, sans-serif",
        lineHeight: 1.6,
      }}
    >
      <h1 style={{ fontSize: 28, fontWeight: 700 }}>Terms of Service</h1>
      <p style={{ opacity: 0.7 }}>Last updated: {UPDATED}</p>

      <p>
        majra is a self-hosted marketing-analytics tool provided to its account
        operator on an &quot;as is&quot; basis. By connecting an advertising
        platform you confirm that you are authorized to grant read access to the
        accounts you connect.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>Use</h2>
      <p>
        The Service reads reporting data from platforms you authorize and
        displays it back to you. You are responsible for complying with each
        connected platform&apos;s own terms and for the accounts you connect.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>
        No warranty
      </h2>
      <p>
        Reporting figures are provided by the connected platforms and shown
        without warranty of accuracy, availability, or fitness for a particular
        purpose. The operator is not liable for decisions made based on the
        data.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>
        Termination
      </h2>
      <p>
        You may disconnect any platform and stop using the Service at any time
        from the Connections page.
      </p>

      <hr style={{ margin: "40px 0", opacity: 0.2 }} />

      <h1 style={{ fontSize: 28, fontWeight: 700 }} dir="rtl">
        شروط الاستخدام
      </h1>
      <p dir="rtl">
        مجرى أداة تحليلات تسويقية ذاتية الاستضافة تُقدَّم لمشغّل الحساب &quot;كما
        هي&quot;. بربطك لأي منصة إعلانية فإنك تؤكد أنك مخوّل بمنح صلاحية القراءة
        للحسابات التي تربطها. تقرأ الخدمة بيانات التقارير من المنصات التي تصرّح
        لها وتعرضها لك. تُعرض الأرقام دون ضمان دقتها، وأنت مسؤول عن الالتزام بشروط
        كل منصة مرتبطة. يمكنك فصل أي منصة وإيقاف استخدام الخدمة في أي وقت من صفحة
        الاتصالات.
      </p>
    </main>
  );
}
