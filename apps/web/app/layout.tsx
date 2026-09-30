import "./globals.css";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = {
  title: "Backchannel Experiment Dashboard",
  description: "Baseline vs backchannel-enabled LiveKit agent, latency and behaviour comparison",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <span className="brand">Backchannel Experiment</span>
          <Link href="/overview">Overview</Link>
          <Link href="/scenarios">Scenarios</Link>
          <Link href="/compare">Compare</Link>
          <Link href="/live">Live Demo</Link>
          <Link href="/live-sessions">Live Sessions</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
