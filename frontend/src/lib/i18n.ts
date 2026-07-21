/**
 * Bilingual UI: English and Arabic.
 *
 * Two formatting decisions worth stating, because the defaults are wrong here:
 *
 * 1. NUMBERS STAY IN WESTERN DIGITS. Plain `ar-SA` renders 12,345.67 as
 *    ١٢٬٣٤٥٫٦٧. Arabic-Indic numerals are correct in prose, but Gulf business and
 *    financial reporting uses Western digits — a spend figure in a marketing
 *    dashboard should read the same in both languages. Hence `-nu-latn`.
 *
 * 2. DATES STAY GREGORIAN. Browser ICU may default `ar-SA` to the Hijri
 *    (Umm al-Qura) calendar. Ad platforms report in Gregorian days; showing a
 *    campaign date as 15 ذو الحجة would be actively misleading. Hence `-ca-gregory`.
 */
export type Locale = "en" | "ar";

export const LOCALES: Locale[] = ["en", "ar"];
export const DEFAULT_LOCALE: Locale = "en";

export const DIR: Record<Locale, "ltr" | "rtl"> = { en: "ltr", ar: "rtl" };

/** Latin digits, Gregorian calendar — in both languages. See the note above. */
const INTL_LOCALE: Record<Locale, string> = {
  en: "en-US",
  ar: "ar-SA-u-nu-latn-ca-gregory",
};

type Dict = Record<string, string>;

const en: Dict = {
  // shell
  "app.tagline": "One channel",
  "app.subtitle": "Every platform's numbers, in one channel.",
  "nav.explore": "Explore",
  "nav.connections": "Connections",
  "nav.jobs": "Sync jobs",
  "nav.reports": "Reports",
  "nav.settings": "Settings",
  "nav.signOut": "Sign out",
  "nav.language": "العربية",

  // auth
  "auth.email": "Email",
  "auth.password": "Password",
  "auth.signIn": "Sign in",
  "auth.createAccount": "Create account",
  "auth.toRegister": "No account yet? Create one",
  "auth.toLogin": "Already have an account? Sign in",
  "auth.minChars": "At least 8 characters.",
  "auth.failed": "That didn't work. Check your details and try again.",
  "auth.checking": "Checking your session…",

  // common
  "common.working": "Working…",
  "common.loading": "Loading",
  "common.close": "Close",
  "common.copy": "Copy",
  "common.copied": "Copied",
  "common.from": "From",
  "common.to": "To",
  "common.showAll": "Show all",
  "common.download": "Download",
  "common.never": "never",
  "common.justNow": "just now",
  "common.minutesAgo": "{n}m ago",
  "common.hoursAgo": "{n}h ago",
  "common.daysAgo": "{n}d ago",

  // statuses
  "status.success": "success",
  "status.failed": "failed",
  "status.running": "running",
  "status.pending": "pending",
  "status.active": "active",
  "status.needs_reauth": "needs reauth",
  "status.disabled": "disabled",

  // datasets
  "dataset.ads": "Ads",
  "dataset.web": "Web",
  "dataset.social": "Social",
  "dataset.tags": "Tags",
  "dataset.ads.note": "Meta · Google · TikTok · Snapchat",
  "dataset.web.note": "Google Analytics 4",
  "dataset.social.note": "Facebook & Instagram",
  "dataset.tags.note": "Tag Manager",

  // explore
  "explore.title": "Explore",
  "explore.platforms": "Platforms",
  "explore.overTime": "{metric} over time",
  "explore.byPlatform": "{metric} by platform",
  "explore.through": "through {date}",
  "explore.staleTitle":
    "This platform has no data yet for the end of the selected range — its sync has not caught up, so the figure is incomplete rather than low.",
  "explore.zeroPlatformHint":
    "A platform showing zero returned data for this range but no spend — it ran no paid delivery, or its sync has not caught up yet.",
  "explore.breakdown": "Breakdown",
  "explore.groupBy": "Group by",
  "explore.noData": "No data in this range.",
  "explore.noDataHint": "Connect a platform and run a sync, or widen the dates.",
  "explore.pickGroup": "Pick something to group by.",
  "explore.pickGroupHint": "Campaign, platform, country — whatever you want to compare.",
  "explore.nothingForGrouping": "Nothing to show for this grouping.",
  "explore.loadFailed": "Could not load these numbers.",
  "explore.tagsEmpty": "No Tag Manager snapshot yet.",
  "explore.tagsEmptyHint": "Connect Tag Manager and run a sync to capture your container.",
  "explore.captured": "Tag Manager · captured {when}",
  "explore.kind": "Kind",
  "explore.name": "Name",
  "explore.type": "Type",
  "explore.container": "Container",

  // measures
  "m.spend": "Spend",
  "m.impressions": "Impressions",
  "m.clicks": "Clicks",
  "m.ctr": "CTR",
  "m.cpc": "CPC",
  "m.cpm": "CPM",
  "m.cpa": "CPA",
  "m.conversions": "Conversions",
  "m.conversion_value": "Value",
  "m.roas": "ROAS",
  "m.video_views": "Video views",
  "m.leads": "Leads",
  "m.purchases": "Purchases",
  "m.sessions": "Sessions",
  "m.users": "Users",
  "m.revenue": "Revenue",
  "m.reach": "Reach",
  "m.engagement": "Engagement",
  "m.followers": "Followers",

  // group keys
  "g.platform": "Platform",
  "g.date": "Date",
  "g.account": "Account",
  "g.campaign": "Campaign",
  "g.adset": "Ad set",
  "g.ad": "Ad",
  "g.country": "Country",
  "g.device": "Device",
  "g.source": "Source",
  "g.medium": "Medium",
  "g.grain": "Grain",
  "g.external_ref": "Reference",

  // connections
  "conn.title": "Connections",
  "conn.subtitle":
    "Each platform is a tributary. A gap in a channel means data didn't arrive that day.",
  "conn.add": "Add connection",
  "conn.addTitle": "Add a connection",
  "conn.modeSelf": "I have the login",
  "conn.modeInvite": "Send a link",
  "conn.selfHint": "Authorise an account you can sign into yourself.",
  "conn.inviteHint":
    "Pick a platform to generate a link. Whoever opens it authorises their own account — you never see their password.",
  "conn.inviteReady": "{platform} invite link — send this to your client",
  "conn.inviteExpiry":
    "Works for {days} days. Treat it like a password: anyone with the link can attach an account to this workspace.",
  "conn.none": "No connections yet.",
  "conn.noneHint": "Add a platform to start pulling its numbers into majra.",
  "conn.noAccounts": "No accounts discovered yet — run a sync.",
  "conn.syncNow": "Sync now",
  "conn.syncDaily": "Sync daily",
  "conn.pauseDaily": "Pause daily sync",
  "conn.resumeDaily": "Resume daily sync",
  "conn.manualOnly": "Manual only",
  "conn.scheduleState": "{state} · last run {when}",
  "conn.daily": "Daily",
  "conn.paused": "Paused",
  "conn.syncStarted": "Sync started — watch it on the Sync jobs page.",
  "conn.loadFailed": "Could not load your connections.",
  "conn.connected": "Connected",
  "conn.authorize": "Authorize",
  "conn.serviceAccount": "Service account",
  "conn.saHint":
    "{platform} reads through a Google service account. Grant it access in Google, then paste its key file here.",
  "conn.saKey": "Service account key (JSON)",
  "conn.propertyId": "GA4 property ID",
  "conn.save": "Save connection",
  "conn.badJson": "That isn't valid JSON. Paste the whole key file.",
  "conn.daysNoGaps": "{n} days, no gaps",
  "conn.daysMissing": "{n} days missing",
  "conn.dayMissing": "1 day missing",

  // jobs
  "jobs.title": "Sync jobs",
  "jobs.subtitle": "Every pull majra has made. Open one to see what it did, step by step.",
  "jobs.none": "No syncs yet.",
  "jobs.noneHint": "Connect a platform, then choose Sync now.",
  "jobs.range": "Range",
  "jobs.kind": "Kind",
  "jobs.status": "Status",
  "jobs.rows": "Rows",
  "jobs.when": "When",
  "jobs.stats":
    "{accounts} accounts · {fetched} fetched · {deleted} replaced · {inserted} written",
  "jobs.loadingSteps": "Loading steps…",
  "jobs.noSteps": "No steps recorded.",
  "jobs.loadFailed": "Could not load sync jobs.",

  // reports
  "rep.title": "Reports",
  "rep.subtitle":
    "A query that runs on a schedule and delivers itself — to a Google Sheet, a webhook, or your inbox.",
  "rep.new": "New report",
  "rep.none": "No reports yet.",
  "rep.noneHint": "Set one up and yesterday's numbers will be waiting for you each morning.",
  "rep.summary": "{dataset} · last {days} days · {cadence} at {hour}:00 UTC → {dest}",
  "rep.runNow": "Run now",
  "rep.pause": "Pause",
  "rep.resume": "Resume",
  "rep.delete": "Delete",
  "rep.ran": "ran {when}",
  "rep.neverRun": "never run",
  "rep.name": "Name",
  "rep.namePlaceholder": "Daily spend by platform",
  "rep.data": "Data",
  "rep.window": "Rolling window (days)",
  "rep.sendAt": "Send daily at (UTC)",
  "rep.deliverTo": "Deliver to",
  "rep.dest.google_sheets": "Google Sheet",
  "rep.dest.webhook": "Webhook",
  "rep.dest.email": "Email",
  "rep.sheetHint":
    "Share the sheet with your service account's email, giving it Editor access. majra clears the tab and rewrites it on every run.",
  "rep.spreadsheetId": "Spreadsheet ID",
  "rep.tabName": "Tab name",
  "rep.url": "URL",
  "rep.secret": "Signing secret (optional)",
  "rep.secretHint": "We'll sign each payload so you can verify it came from majra.",
  "rep.sendToLabel": "Send to",
  "rep.smtpHint": "Needs SMTP configured on the server.",
  "rep.save": "Save report",
  "rep.saveFailed": "Could not save that report.",
  "rep.deliverFailed": "That report didn't deliver.",
  "rep.loadFailed": "Could not load your reports.",
  "rep.badSaJson": "That service-account JSON isn't valid. Paste the whole key file.",
  "rep.daily": "daily",

  // settings
  "set.title": "Settings",
  "set.account": "Account",
  "set.joined": "Joined",
  "set.keys": "API keys ({n})",
  "set.keysBlurb":
    "A key reads your numbers from your own scripts — it can call /metrics and /exports, and nothing else. It can't start a sync, add a connection, or make another key.",
  "set.keyOnce": "Copy this key now — majra stores only its hash, so it can't be shown again.",
  "set.copy": "Copy",
  "set.copied": "Copied",
  "set.done": "Done",
  "set.keyPurpose": "What is this key for?",
  "set.keyPlaceholder": "Nightly spreadsheet pull",
  "set.createKey": "Create key",
  "set.creating": "Creating…",
  "set.noKeys": "No keys yet.",
  "set.revoke": "Revoke",
  "set.keyUsed": "used {when}",
  "set.keyNeverUsed": "never used",
  "set.keyFailed": "Could not create that key.",
  "set.accountsVisible": "Accounts majra can see ({n})",
  "set.noAccounts": "None yet. Connect a platform and run a sync.",

  // platforms
  "p.meta": "Meta Ads",
  "p.google_ads": "Google Ads",
  "p.tiktok": "TikTok Ads",
  "p.snapchat": "Snapchat Ads",
  "p.ga4": "Google Analytics 4",
  "p.meta_social": "Facebook & Instagram",
  "p.gtm": "Tag Manager",
};

const ar: Dict = {
  // shell
  "app.tagline": "مجرى واحد",
  "app.subtitle": "أرقام كل المنصات، في مجرى واحد.",
  "nav.explore": "استكشاف",
  "nav.connections": "الاتصالات",
  "nav.jobs": "مهام المزامنة",
  "nav.reports": "التقارير",
  "nav.settings": "الإعدادات",
  "nav.signOut": "تسجيل الخروج",
  "nav.language": "English",

  // auth
  "auth.email": "البريد الإلكتروني",
  "auth.password": "كلمة المرور",
  "auth.signIn": "تسجيل الدخول",
  "auth.createAccount": "إنشاء حساب",
  "auth.toRegister": "ليس لديك حساب؟ أنشئ واحدًا",
  "auth.toLogin": "لديك حساب بالفعل؟ سجّل الدخول",
  "auth.minChars": "٨ أحرف على الأقل.",
  "auth.failed": "لم ينجح ذلك. تحقق من بياناتك وحاول مرة أخرى.",
  "auth.checking": "جارٍ التحقق من جلستك…",

  // common
  "common.working": "جارٍ العمل…",
  "common.loading": "جارٍ التحميل",
  "common.close": "إغلاق",
  "common.copy": "نسخ",
  "common.copied": "تم النسخ",
  "common.from": "من",
  "common.to": "إلى",
  "common.showAll": "عرض الكل",
  "common.download": "تنزيل",
  "common.never": "أبدًا",
  "common.justNow": "الآن",
  "common.minutesAgo": "قبل {n} دقيقة",
  "common.hoursAgo": "قبل {n} ساعة",
  "common.daysAgo": "قبل {n} يوم",

  // statuses
  "status.success": "نجحت",
  "status.failed": "فشلت",
  "status.running": "قيد التشغيل",
  "status.pending": "قيد الانتظار",
  "status.active": "نشط",
  "status.needs_reauth": "يحتاج إعادة تفويض",
  "status.disabled": "معطّل",

  // datasets
  "dataset.ads": "الإعلانات",
  "dataset.web": "الويب",
  "dataset.social": "التواصل الاجتماعي",
  "dataset.tags": "الوسوم",
  "dataset.ads.note": "ميتا · جوجل · تيك توك · سناب شات",
  "dataset.web.note": "تحليلات جوجل ٤",
  "dataset.social.note": "فيسبوك وإنستغرام",
  "dataset.tags.note": "مدير الوسوم",

  // explore
  "explore.title": "استكشاف",
  "explore.platforms": "المنصات",
  "explore.overTime": "{metric} عبر الزمن",
  "explore.byPlatform": "{metric} حسب المنصة",
  "explore.through": "حتى {date}",
  "explore.staleTitle":
    "لا توجد بيانات لهذه المنصة حتى نهاية الفترة المحددة — لم تكتمل مزامنتها بعد، لذا الرقم ناقص وليس منخفضاً.",
  "explore.zeroPlatformHint":
    "المنصة التي تظهر صفراً أعادت بيانات لهذه الفترة لكن دون إنفاق — إما لم تُشغّل إعلانات مدفوعة، أو أن مزامنتها لم تكتمل بعد.",
  "explore.breakdown": "التفصيل",
  "explore.groupBy": "التجميع حسب",
  "explore.noData": "لا توجد بيانات في هذه الفترة.",
  "explore.noDataHint": "اربط منصة وشغّل مزامنة، أو وسّع نطاق التواريخ.",
  "explore.pickGroup": "اختر معيارًا للتجميع.",
  "explore.pickGroupHint": "الحملة، المنصة، الدولة — أي شيء تريد مقارنته.",
  "explore.nothingForGrouping": "لا شيء لعرضه بهذا التجميع.",
  "explore.loadFailed": "تعذّر تحميل هذه الأرقام.",
  "explore.tagsEmpty": "لا توجد لقطة من مدير الوسوم بعد.",
  "explore.tagsEmptyHint": "اربط مدير الوسوم وشغّل مزامنة لالتقاط الحاوية.",
  "explore.captured": "مدير الوسوم · التُقطت في {when}",
  "explore.kind": "النوع",
  "explore.name": "الاسم",
  "explore.type": "الصنف",
  "explore.container": "الحاوية",

  // measures
  "m.spend": "الإنفاق",
  "m.impressions": "مرات الظهور",
  "m.clicks": "النقرات",
  "m.ctr": "نسبة النقر",
  "m.cpc": "تكلفة النقرة",
  "m.cpm": "تكلفة الألف ظهور",
  "m.cpa": "تكلفة الاكتساب",
  "m.conversions": "التحويلات",
  "m.conversion_value": "القيمة",
  "m.roas": "العائد على الإنفاق",
  "m.video_views": "مشاهدات الفيديو",
  "m.leads": "العملاء المحتملون",
  "m.purchases": "المشتريات",
  "m.sessions": "الجلسات",
  "m.users": "المستخدمون",
  "m.revenue": "الإيرادات",
  "m.reach": "الوصول",
  "m.engagement": "التفاعل",
  "m.followers": "المتابعون",

  // group keys
  "g.platform": "المنصة",
  "g.date": "التاريخ",
  "g.account": "الحساب",
  "g.campaign": "الحملة",
  "g.adset": "المجموعة الإعلانية",
  "g.ad": "الإعلان",
  "g.country": "الدولة",
  "g.device": "الجهاز",
  "g.source": "المصدر",
  "g.medium": "الوسيط",
  "g.grain": "المستوى",
  "g.external_ref": "المرجع",

  // connections
  "conn.title": "الاتصالات",
  "conn.subtitle": "كل منصة رافد. الفجوة في المجرى تعني أن البيانات لم تصل ذلك اليوم.",
  "conn.add": "إضافة اتصال",
  "conn.addTitle": "إضافة اتصال",
  "conn.modeSelf": "لديّ بيانات الدخول",
  "conn.modeInvite": "إرسال رابط",
  "conn.selfHint": "اربط حساباً يمكنك تسجيل الدخول إليه بنفسك.",
  "conn.inviteHint":
    "اختر منصة لإنشاء رابط. من يفتحه يمنح الإذن لحسابه بنفسه — ولن ترى كلمة مروره أبداً.",
  "conn.inviteReady": "رابط دعوة {platform} — أرسله إلى عميلك",
  "conn.inviteExpiry":
    "صالح لمدة {days} أيام. تعامل معه ككلمة مرور: أي شخص يملك الرابط يستطيع ربط حساب بمساحة العمل هذه.",
  "conn.none": "لا توجد اتصالات بعد.",
  "conn.noneHint": "أضف منصة لتبدأ بسحب أرقامها إلى مجرى.",
  "conn.noAccounts": "لم يُعثر على حسابات بعد — شغّل مزامنة.",
  "conn.syncNow": "مزامنة الآن",
  "conn.syncDaily": "مزامنة يومية",
  "conn.pauseDaily": "إيقاف المزامنة اليومية",
  "conn.resumeDaily": "استئناف المزامنة اليومية",
  "conn.manualOnly": "يدوي فقط",
  "conn.scheduleState": "{state} · آخر تشغيل {when}",
  "conn.daily": "يومي",
  "conn.paused": "متوقف",
  "conn.syncStarted": "بدأت المزامنة — تابعها في صفحة مهام المزامنة.",
  "conn.loadFailed": "تعذّر تحميل اتصالاتك.",
  "conn.connected": "متصل",
  "conn.authorize": "تفويض",
  "conn.serviceAccount": "حساب خدمة",
  "conn.saHint":
    "{platform} تقرأ عبر حساب خدمة من جوجل. امنحه صلاحية الوصول في جوجل، ثم الصق ملف المفتاح هنا.",
  "conn.saKey": "مفتاح حساب الخدمة (JSON)",
  "conn.propertyId": "معرّف خاصية GA4",
  "conn.save": "حفظ الاتصال",
  "conn.badJson": "هذا ليس JSON صالحًا. الصق ملف المفتاح كاملًا.",
  "conn.daysNoGaps": "{n} يومًا، بلا فجوات",
  "conn.daysMissing": "{n} أيام ناقصة",
  "conn.dayMissing": "يوم واحد ناقص",

  // jobs
  "jobs.title": "مهام المزامنة",
  "jobs.subtitle": "كل عملية سحب قام بها مجرى. افتح واحدة لترى ما فعلته، خطوة بخطوة.",
  "jobs.none": "لا توجد عمليات مزامنة بعد.",
  "jobs.noneHint": "اربط منصة، ثم اختر «مزامنة الآن».",
  "jobs.range": "الفترة",
  "jobs.kind": "النوع",
  "jobs.status": "الحالة",
  "jobs.rows": "الصفوف",
  "jobs.when": "متى",
  "jobs.stats": "{accounts} حسابات · {fetched} مسحوبة · {deleted} مستبدلة · {inserted} مكتوبة",
  "jobs.loadingSteps": "جارٍ تحميل الخطوات…",
  "jobs.noSteps": "لم تُسجّل أي خطوات.",
  "jobs.loadFailed": "تعذّر تحميل مهام المزامنة.",

  // reports
  "rep.title": "التقارير",
  "rep.subtitle":
    "استعلام يعمل وفق جدول ويرسل نفسه — إلى جدول بيانات جوجل، أو webhook، أو بريدك.",
  "rep.new": "تقرير جديد",
  "rep.none": "لا توجد تقارير بعد.",
  "rep.noneHint": "أنشئ واحدًا وستجد أرقام الأمس بانتظارك كل صباح.",
  "rep.summary": "{dataset} · آخر {days} يومًا · {cadence} الساعة {hour}:00 بتوقيت UTC ← {dest}",
  "rep.runNow": "تشغيل الآن",
  "rep.pause": "إيقاف مؤقت",
  "rep.resume": "استئناف",
  "rep.delete": "حذف",
  "rep.ran": "شُغّل {when}",
  "rep.neverRun": "لم يُشغّل بعد",
  "rep.name": "الاسم",
  "rep.namePlaceholder": "الإنفاق اليومي حسب المنصة",
  "rep.data": "البيانات",
  "rep.window": "النافذة المتحركة (أيام)",
  "rep.sendAt": "الإرسال يوميًا عند (UTC)",
  "rep.deliverTo": "الإرسال إلى",
  "rep.dest.google_sheets": "جدول بيانات جوجل",
  "rep.dest.webhook": "Webhook",
  "rep.dest.email": "البريد الإلكتروني",
  "rep.sheetHint":
    "شارك الجدول مع بريد حساب الخدمة ومنحه صلاحية التحرير. يمسح مجرى التبويب ويعيد كتابته في كل تشغيل.",
  "rep.spreadsheetId": "معرّف جدول البيانات",
  "rep.tabName": "اسم التبويب",
  "rep.url": "الرابط",
  "rep.secret": "مفتاح التوقيع (اختياري)",
  "rep.secretHint": "سنوقّع كل حمولة لتتمكن من التحقق أنها جاءت من مجرى.",
  "rep.sendToLabel": "الإرسال إلى",
  "rep.smtpHint": "يتطلب إعداد SMTP على الخادم.",
  "rep.save": "حفظ التقرير",
  "rep.saveFailed": "تعذّر حفظ هذا التقرير.",
  "rep.deliverFailed": "لم يُسلَّم هذا التقرير.",
  "rep.loadFailed": "تعذّر تحميل تقاريرك.",
  "rep.badSaJson": "ملف حساب الخدمة غير صالح. الصق الملف كاملًا.",
  "rep.daily": "يومي",

  // settings
  "set.title": "الإعدادات",
  "set.account": "الحساب",
  "set.joined": "تاريخ الانضمام",
  "set.keys": "مفاتيح الواجهة البرمجية ({n})",
  "set.keysBlurb":
    "المفتاح يقرأ أرقامك من سكربتاتك الخاصة — يمكنه استدعاء /metrics و /exports فقط. لا يستطيع بدء مزامنة، ولا إضافة اتصال، ولا إنشاء مفتاح آخر.",
  "set.keyOnce": "انسخ هذا المفتاح الآن — يخزّن مجرى بصمته فقط، ولا يمكن عرضه مرة أخرى.",
  "set.copy": "نسخ",
  "set.copied": "تم النسخ",
  "set.done": "تم",
  "set.keyPurpose": "ما الغرض من هذا المفتاح؟",
  "set.keyPlaceholder": "سحب ليلي إلى جدول بيانات",
  "set.createKey": "إنشاء مفتاح",
  "set.creating": "جارٍ الإنشاء…",
  "set.noKeys": "لا توجد مفاتيح بعد.",
  "set.revoke": "إبطال",
  "set.keyUsed": "استُخدم {when}",
  "set.keyNeverUsed": "لم يُستخدم",
  "set.keyFailed": "تعذّر إنشاء هذا المفتاح.",
  "set.accountsVisible": "الحسابات التي يراها مجرى ({n})",
  "set.noAccounts": "لا شيء بعد. اربط منصة وشغّل مزامنة.",

  // platforms
  "p.meta": "إعلانات ميتا",
  "p.google_ads": "إعلانات جوجل",
  "p.tiktok": "إعلانات تيك توك",
  "p.snapchat": "إعلانات سناب شات",
  "p.ga4": "تحليلات جوجل ٤",
  "p.meta_social": "فيسبوك وإنستغرام",
  "p.gtm": "مدير الوسوم",
};

export const DICTS: Record<Locale, Dict> = { en, ar };

export type Vars = Record<string, string | number>;

/** Look a key up, falling back to English, then to the key itself. */
export function translate(locale: Locale, key: string, vars?: Vars): string {
  const raw = DICTS[locale][key] ?? DICTS.en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (_, name) =>
    name in vars ? String(vars[name]) : `{${name}}`,
  );
}

// --------------------------------------------------------------- formatting
const MONEY = new Set(["spend", "cpc", "cpm", "cpa", "conversion_value", "revenue"]);

export function formatNumber(
  locale: Locale,
  value: number,
  opts?: Intl.NumberFormatOptions,
): string {
  return value.toLocaleString(INTL_LOCALE[locale], opts);
}

export function formatMeasure(locale: Locale, key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  if (key === "ctr") return `${formatNumber(locale, n, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  if (key === "roas") return formatNumber(locale, n, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (MONEY.has(key)) {
    return formatNumber(locale, n, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return formatNumber(locale, Math.round(n));
}

/** ISO date in, short Gregorian date out — never Hijri. */
export function formatDate(locale: Locale, iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(INTL_LOCALE[locale], {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export function timeAgo(locale: Locale, iso: string | null): string {
  if (!iso) return translate(locale, "common.never");
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return translate(locale, "common.justNow");
  if (mins < 60) return translate(locale, "common.minutesAgo", { n: mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return translate(locale, "common.hoursAgo", { n: hours });
  return translate(locale, "common.daysAgo", { n: Math.round(hours / 24) });
}

/** Measure and group-key labels resolve through the dictionary. */
export const measureLabel = (locale: Locale, key: string) => translate(locale, `m.${key}`);
export const groupLabel = (locale: Locale, key: string) => translate(locale, `g.${key}`);
export const platformName = (locale: Locale, key: string) => translate(locale, `p.${key}`);
export const statusLabel = (locale: Locale, key: string) => translate(locale, `status.${key}`);
