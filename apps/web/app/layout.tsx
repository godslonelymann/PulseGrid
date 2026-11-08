import type { Metadata } from "next";
import { Activity } from "lucide-react";
import "./globals.css";
export const metadata: Metadata = {
  title: "PulseGrid · Self-hosted uptime monitoring",
  description:
    "Monitor websites and HTTP APIs from your own server. Track response times and get outage and recovery alerts.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="site-content">{children}</div>
        <footer className="page-footer">
          <div className="page-footer-inner">
            <span>
              <Activity size={14} aria-hidden="true" />
              PulseGrid
            </span>
            <span>A clearer picture.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
