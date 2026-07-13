/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // distDir stays the default ".next" (Next.js rejects absolute/out-of-project
  // paths). dev.sh symlinks dashboard/.next -> ~/.cache/mo_pipeline/next so the
  // build cache physically lives outside Dropbox without churning it.
  // Proxy /api/* to the FastAPI orchestrator so the browser hits one origin.
  async rewrites() {
    const port = process.env.MO_API_PORT || "8090";
    return [{ source: "/api/:path*", destination: `http://127.0.0.1:${port}/:path*` }];
  },
};
export default nextConfig;
