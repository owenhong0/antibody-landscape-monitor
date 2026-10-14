import Link from "next/link";
import { Suspense } from "react";
import { apiGet, TARGETS, type AntibodySummary, type Landscape, type Overview } from "@/lib/api";

// Colored bar on top of each column: blues deepen toward the clinic, approved is green.
const STAGE_ACCENT: Record<string, string> = {
  preclinical: "#94a3b8",
  phase_1: "#93c5fd",
  phase_2: "#60a5fa",
  phase_3: "#3b82f6",
  filed: "#1d4ed8",
  approved: "#047857",
  unknown: "#9ca3af",
};

type Props = { searchParams: Promise<{ target?: string }> };

// The page itself stays synchronous so Next can prerender the shell (nav +
// skeleton). Everything that needs the request (searchParams, API data) lives
// inside the Suspense boundary and streams in.
export default function LandscapePage({ searchParams }: Props) {
  return (
    <Suspense fallback={<LandscapeSkeleton />}>
      <LandscapeView searchParams={searchParams} />
    </Suspense>
  );
}

async function LandscapeView({ searchParams }: Props) {
  const { target = "PD-1" } = await searchParams;
  const [landscape, overview] = await Promise.all([
    apiGet<Landscape>(`/api/landscape?target=${encodeURIComponent(target)}`),
    apiGet<Overview>("/api/landscape"),
  ]);
  const totals = Object.fromEntries(overview.targets.map((t) => [t.target, t.total]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-ink">
            {landscape.target} landscape
            <span className="ml-2 text-base font-normal text-muted">{landscape.total} antibodies</span>
          </h1>
          <p className="mt-1 text-sm text-muted">
            Grouped by development stage. Open a card for sequences, liabilities and trials.
          </p>
        </div>
        <p className="text-sm text-muted">
          {overview.antibodies_total} antibodies tracked across {TARGETS.length} targets
        </p>
      </div>

      <TargetPicker active={landscape.target} totals={totals} />

      <div className="overflow-x-auto pb-2">
        <div className="grid min-w-[1150px] grid-cols-7 gap-3">
          {landscape.columns.map((col) => (
            <section
              key={col.stage}
              className="rounded-lg border border-line border-t-4 bg-panel"
              style={{ borderTopColor: STAGE_ACCENT[col.stage] }}
            >
              <h2 className="flex items-baseline justify-between px-3 py-2 text-sm font-semibold text-ink">
                {col.label}
                <span className="tabular-nums text-muted">{col.count}</span>
              </h2>
              <ul className="space-y-2 px-2 pb-2">
                {col.antibodies.length === 0 && (
                  <li className="px-1 py-3 text-center text-xs text-muted">None</li>
                )}
                {col.antibodies.map((ab) => (
                  <li key={ab.id}>
                    <AntibodyCard ab={ab} current={landscape.target} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

function TargetPicker({ active, totals }: { active: string; totals: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-2">
      {TARGETS.map((t) => {
        const isActive = t === active;
        return (
          <Link
            key={t}
            href={`/?target=${encodeURIComponent(t)}`}
            aria-current={isActive ? "page" : undefined}
            className={`rounded-full border px-3 py-1 text-sm font-medium ${
              isActive
                ? "border-accent bg-accent text-on-accent"
                : "border-line bg-panel text-ink hover:border-accent"
            }`}
          >
            {t}
            <span className={`ml-1.5 tabular-nums ${isActive ? "opacity-80" : "text-muted"}`}>
              {totals[t] ?? 0}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function AntibodyCard({ ab, current }: { ab: AntibodySummary; current: string }) {
  const multiTarget = ab.targets.length > 1;
  return (
    <Link
      href={`/antibodies/${ab.id}`}
      className="block rounded-md border border-line bg-canvas px-2.5 py-2 hover:border-accent"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="truncate text-sm font-medium text-ink" title={ab.display_name}>
          {ab.display_name}
        </span>
        {ab.status === "discontinued" && (
          <span className="shrink-0 rounded bg-warn-soft px-1.5 text-[10px] font-medium uppercase text-warn">
            disc.
          </span>
        )}
      </div>
      {ab.format && (
        <div className="truncate text-xs text-muted" title={ab.format}>
          {ab.format}
        </div>
      )}
      {multiTarget && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {ab.targets.map((t) => (
            <span
              key={t}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                t === current ? "bg-accent-soft text-accent" : "border border-line text-muted"
              }`}
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </Link>
  );
}

function LandscapeSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true">
      <div className="h-8 w-64 animate-pulse rounded bg-line" />
      <div className="flex gap-2">
        {TARGETS.map((t) => (
          <div key={t} className="h-8 w-20 animate-pulse rounded-full bg-line" />
        ))}
      </div>
      <div className="grid min-w-[1150px] grid-cols-7 gap-3">
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className="h-96 animate-pulse rounded-lg border border-line bg-panel" />
        ))}
      </div>
    </div>
  );
}