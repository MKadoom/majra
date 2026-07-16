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
    ];
  },
};
export default nextConfig;
