import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";
// No third-party scripts, styles, fonts, or API hosts. Google sign-in is a top-level
// navigation, so it needs no connect-src or form-action entry.
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(isDev ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const config: NextConfig = {
  output: "standalone",
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async rewrites() {
    return process.env.NODE_ENV === "development"
      ? [
          {
            source: "/api/v1/:path*",
            destination: `${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/api/v1/:path*`,
          },
        ]
      : [];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
        ],
      },
    ];
  },
};
export default config;
