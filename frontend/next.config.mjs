// Public machine API (/v1/*): API-key read endpoints used by the MCP server,
// Looker Studio and Sheets. Anything not listed here 404s at the edge.
const V1_READ_PATHS = [
  "/metrics",
  "/web-metrics",
  "/social-metrics",
  "/accounts",
  "/gtm-resources",
  "/export",
  "/freshness",
  "/pacing",
  "/anomalies",
  "/reconciliation",
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    // On Railway the frontend is the only public service; the OAuth providers
    // redirect back to {PUBLIC_BASE_URL}/connect/<platform>/callback, which is
    // served by the backend. Forward those to the backend over the private
    // network so the token exchange completes instead of hitting a 404.
    const api = process.env.MAJRA_API_URL || "http://backend.railway.internal:8000";
    return [
      { source: "/connect/:path*", destination: `${api}/connect/:path*` },
      // Public, API-key-authenticated surface. The browser app talks to the
      // backend through /api/proxy (which attaches the session cookie); this is
      // the other door — for machines. Headers pass through untouched, so an
      // `Authorization: Bearer <majra api key>` reaches the backend as-is.
      // This is what the MCP server, Looker Studio and Sheets connect to.
      // Only an explicit allow-list of READ paths is forwarded — never auth,
      // connections, FX writes, API docs or /_metrics. Each path is exact (no
      // sub-paths), and every one is GET-only on the backend.
      ...V1_READ_PATHS.map((p) => ({ source: `/v1${p}`, destination: `${api}${p}` })),
      // Client-facing invite pages: someone with a link authorises their own ad
      // account without a majra login, so these must be public and are served
      // by the backend, not the app shell.
      { source: "/invite/:path*", destination: `${api}/invite/:path*` },
    ];
  },
};
export default nextConfig;
