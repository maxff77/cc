/** @type {import('next').NextConfig} */
const nextConfig = {
  // Dev-only: allow loading dev resources (HMR, client chunks, fonts) when the
  // app is opened on 127.0.0.1 as well as localhost. Without this, Next 16
  // blocks cross-origin dev requests, the page never hydrates, and forms fall
  // back to a native GET submit. No effect in production.
  allowedDevOrigins: ["127.0.0.1"],
  // Dev proxy: forward API + WebSocket traffic to the FastAPI backend on :8000.
  // In production Caddy routes /api and /ws directly to uvicorn (Story 1.7),
  // so these rewrites only matter for local development.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
      {
        source: "/ws",
        destination: "http://127.0.0.1:8000/ws",
      },
    ];
  },
};

export default nextConfig;
