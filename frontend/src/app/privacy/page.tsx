import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy · سياسة الخصوصية · majra",
  description: "How majra handles data.",
};

const UPDATED = "2026-07-16";

export default function PrivacyPage() {
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
      <h1 style={{ fontSize: 28, fontWeight: 700 }}>Privacy Policy</h1>
      <p style={{ opacity: 0.7 }}>Last updated: {UPDATED}</p>

      <p>
        majra (&quot;the Service&quot;) is a self-hosted marketing-analytics tool
        operated by its account owner. It connects to advertising platforms you
        authorize and reads reporting data so you can view your own advertising
        performance in one place.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>
        What data we access
      </h2>
      <p>
        When you connect a platform (such as Meta or Snapchat), you grant the
        Service read-only access to that platform&apos;s advertising reporting
        data for accounts you administer — for example spend, impressions,
        clicks, conversions and campaign metadata. The Service does not request
        access to personal messages, contacts, or content beyond advertising
        statistics.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>
        How we store it
      </h2>
      <p>
        Access tokens are encrypted at rest. Reporting data is stored in the
        operator&apos;s own database solely to display it back to the
        operator. The Service does not sell data, does not share it with third
        parties, and does not use it for advertising.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>
        Deletion
      </h2>
      <p>
        You can revoke a platform connection at any time from the Connections
        page, which stops all further data access. To request deletion of stored
        data, contact the account operator, or remove the connection and its
        synced records from within the Service.
      </p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 28 }}>Contact</h2>
      <p>
        For any privacy question, contact the operator of this majra instance at
        the email associated with the account.
      </p>

      <hr style={{ margin: "40px 0", opacity: 0.2 }} />

      <h1 style={{ fontSize: 28, fontWeight: 700 }} dir="rtl">
        سياسة الخصوصية
      </h1>
      <p dir="rtl">
        مجرى (&quot;الخدمة&quot;) أداة تحليلات تسويقية ذاتية الاستضافة يديرها مالك
        الحساب. تتصل الخدمة بمنصات الإعلانات التي تصرّح لها بالوصول وتقرأ بيانات
        التقارير فقط لعرض أداء إعلاناتك في مكان واحد.
      </p>
      <p dir="rtl">
        عند ربط منصة، تمنح الخدمة صلاحية قراءة بيانات تقارير الإعلانات (مثل
        الإنفاق والظهور والنقرات والتحويلات وبيانات الحملات) للحسابات التي تديرها.
        لا تطلب الخدمة الوصول إلى الرسائل الشخصية أو جهات الاتصال. تُخزَّن الرموز
        مشفّرة، ولا تُباع البيانات ولا تُشارك مع أطراف خارجية. يمكنك إلغاء أي ربط
        في أي وقت من صفحة الاتصالات لإيقاف الوصول نهائياً.
      </p>
    </main>
  );
}
