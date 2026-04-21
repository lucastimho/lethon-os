import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The FastAPI backend will live on a separate port in dev. Route /api/*
  // to it so the SSE stream is same-origin from the browser's perspective.
  async rewrites() {
    const backend = process.env.LETHON_BACKEND_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
