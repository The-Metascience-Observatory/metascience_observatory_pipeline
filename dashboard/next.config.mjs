/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Keep the build cache out of Dropbox (set by dev.sh).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Proxy /api/* to the FastAPI orchestrator so the browser hits one origin.
  async rewrites() {
    const port = process.env.MO_API_PORT || "8090";
    return [{ source: "/api/:path*", destination: `http://127.0.0.1:${port}/:path*` }];
  },
};
export default nextConfig;
