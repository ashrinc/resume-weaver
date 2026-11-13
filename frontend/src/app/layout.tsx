import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { AuthGate } from "@/components/AuthGate";
import { DisclosureBanner } from "@/components/DisclosureBanner";
import { NavBar } from "@/components/NavBar";
import { Starfield } from "@/components/Starfield";
import "./globals.css";

// Self-hosted at build time by next/font: the browser never contacts Google.
const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "resume tailr",
  description: "Tailor a LaTeX resume to a job description",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body>
        <Starfield />
        <NavBar />
        <DisclosureBanner />
        <main className="main">
          <AuthGate>{children}</AuthGate>
        </main>
      </body>
    </html>
  );
}
