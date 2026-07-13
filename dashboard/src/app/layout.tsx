import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "MO Pipeline",
  description: "Metascience Observatory replication pipeline dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-slate-200 dark:border-slate-800 px-6 py-3 flex items-center gap-6">
          <Link href="/" className="font-semibold tracking-tight">MO Pipeline</Link>
          <nav className="flex gap-4 text-sm text-slate-500">
            <Link href="/" className="hover:text-slate-900 dark:hover:text-slate-100">Pipeline</Link>
            <Link href="/corpus" className="hover:text-slate-900 dark:hover:text-slate-100">Corpus</Link>
          </nav>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
