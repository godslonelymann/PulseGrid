import type { NextConfig } from "next";
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
        ],
      },
    ];
  },
};
export default config;
