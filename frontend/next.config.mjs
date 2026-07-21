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
      { source: "/v1/:path*", destination: `${api}/:path*` },
    ];
  },
};
export default nextConfig;
