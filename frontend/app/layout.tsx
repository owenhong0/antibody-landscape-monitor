import type {Metadata} from "next";
import Link from "next/link";
import {Suspense} from "react";
import {apiGet, type Page, type Signal} from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
    title: "Antibody Landscape Monitor",
    description: "Competitive landscape, developability liabilities and trial signals",
    metadataBase: new URL("https://antibody-landscape-monitor.vercel.app"),
};

const NAV = [
    {href: "/", label: "Landscape"},
    {href: "/signals", label: "Signals"},
    {href: "/runs", label: "Runs"},
];

export default function RootLayout({children}: { children: React.ReactNode }) {
    return (
        // Browser extensions (e.g. Trancy) add attributes to <html> before React
        // hydrates. This silences the mismatch for this element's attributes only.
        <html lang="en" suppressHydrationWarning>
        <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-panel">
            <nav className="mx-auto flex max-w-[1500px] items-center gap-6 px-6 py-3">
                <Link href="/" className="font-semibold text-ink">
                    Antibody Landscape Monitor
                </Link>
                {NAV.map((item) => (
                    <Link key={item.href} href={item.href}
                          className="flex items-center text-sm text-muted hover:text-ink">
                        {item.label}
                        {item.href === "/signals" && (
                            // Streams in on its own; the rest of the layout stays static.
                            <Suspense fallback={null}>
                                <UnseenBadge/>
                            </Suspense>
                        )}
                    </Link>
                ))}
            </nav>
        </header>
        <main className="mx-auto max-w-[1500px] px-6 py-6">{children}</main>
        <footer className="mx-auto max-w-[1500px] border-t border-line px-6 py-4 text-xs text-muted">
            Built by Owen Hong · Data from Thera-SAbDab and ClinicalTrials.gov, refreshed by a
            Python pipeline (Postgres, Flask API, Next.js) ·{" "}
            <a href="https://github.com/owenhong0/antibody-landscape-monitor" className="text-accent hover:underline">
                Source on GitHub
            </a>{" "}
            · Read-only demo
        </footer>
        </body>
        </html>
    );
}

async function UnseenBadge() {
    const total = await unseenCount();
    if (!total) return null;
    return (
        <span className="ml-1.5 rounded-full bg-accent px-1.5 text-xs font-semibold tabular-nums text-on-accent">
      {total}
    </span>
    );
}

/** Unseen signal count, or 0 if the API is down (the nav still renders). */
async function unseenCount(): Promise<number> {
    try {
        const {total} = await apiGet<Page<Signal>>("/api/signals?seen=false&limit=1");
        return total;
    } catch {
        return 0;
    }
}