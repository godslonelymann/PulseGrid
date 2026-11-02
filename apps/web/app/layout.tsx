import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "PulseGrid · Service monitoring",
  description: "Know how your services are doing.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
