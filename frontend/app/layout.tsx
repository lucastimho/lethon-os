import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lethon-Vision — Agent Memory Observatory",
  description:
    "Real-time observability for the Lethon-OS tiered memory controller.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased">
        <div className="min-h-screen flex flex-col">{children}</div>
      </body>
    </html>
  );
}
