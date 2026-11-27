import type { NextConfig } from "next";

// The browser only ever talks to this Next.js server; /api/* is forwarded to the Python backend.
// Same origin means no CORS configuration and the login cookie works without extra settings.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // The dev server refuses scripts requested from any other host, so without this a page opened through an
  // ngrok link never becomes interactive (forms then submit as plain page loads).
  allowedDevOrigins: ["*.ngrok-free.dev", "*.ngrok-free.app", "*.ngrok.app", "*.ngrok.io"],
  // Next's proxy gives up after 30 s by default; a chat answer (graph search + model) can take longer.
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
