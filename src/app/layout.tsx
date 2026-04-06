import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/layout/sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Portal",
  description: "Personal dashboard",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <link rel="stylesheet" href="/glass.css" />
      </head>
      <body className="min-h-full tabular-nums">
        {/* Animated mesh gradient — gives liquid glass something to refract */}
        <div className="lg-mesh" />
        <div className="lg-blob-c" />
        <div className="lg-blob-d" />
        <div className="relative z-10">
          <Sidebar />
          <div className="md:pl-56">
            <main className="min-h-screen p-6 pt-14 md:pt-6">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
