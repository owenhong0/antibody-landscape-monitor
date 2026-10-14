import { Suspense } from "react";
import { apiGet, type Page, type Run, type RunStatus } from "@/lib/api";

const STATUS: Record<RunStatus, { label: string; color: string }> = {
  succeeded: { label: "Succeeded", color: "#10b981" },
  partial: { label: "Partial", color: "#f59e0b" },
  failed: { label: "Failed", color: "#ef4444" },
  running: { label: "Running", color: "#3b82f6" },
};

export default function RunsPage() {
  return (
    <Suspense fallback={<div className="h-96 animate-pulse rounded-lg border border-line bg-panel" />}>
      <RunsView />
    </Suspense>
  );
}

async function RunsView() {
  const page = await apiGet<Page<Run>>("/api/runs?limit=20");
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">
          Pipeline runs <span className="text-base font-normal text-muted">{page.total}</span>
        </h1>
        <p className="mt-1 text-sm text-muted">
          Each run fetches Thera-SAbDab and ClinicalTrials.gov, analyzes chains, and diffs against the previous run.
        </p>
      </div>
      {page.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
          No pipeline runs yet.
        </p>
      ) : (
        <ul className="space-y-3">
          {page.items.map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunCard({ run }: { run: Run }) {
  const status = STATUS[run.status];
  const counts = Object.entries(run.counts).filter((e): e is [string, number] => typeof e[1] === "number");
  const steps = stepSeconds(run.counts);
  const slowest = Math.max(...steps.map(([, s]) => s), 0);

  return (
    <li className="rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5">
        <span className="font-semibold">Run {run.id}</span>
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: status.color }} />
          {status.label}
        </span>
        <span className="text-sm tabular-nums text-muted">started {utc(run.started_at)}</span>
        <span className="ml-auto text-sm tabular-nums">
          {run.status === "running" ? "in progress…" : formatDuration(run.duration_seconds)}
        </span>
      </header>

      <div className="space-y-3 px-4 py-3">
        {counts.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {counts.map(([key, value]) => (
              <span key={key} className="rounded border border-line px-2 py-0.5 text-sm">
                <span className="text-muted">{key.replaceAll("_", " ")}</span>{" "}
                <span className="font-semibold tabular-nums">{value.toLocaleString("en-US")}</span>
              </span>
            ))}
          </div>
        )}

        {steps.length > 0 && (
          <div className="space-y-1">
            <div className="text-xs text-muted">Step timings</div>
            {steps.map(([name, seconds]) => (
              <div key={name} className="flex items-center gap-3 text-xs">
                <span className="w-36 shrink-0 truncate font-mono text-muted">{name}</span>
                <div className="h-2 flex-1 rounded bg-line">
                  <div className="h-2 rounded bg-accent" style={{ width: `${(seconds / slowest) * 100}%` }} />
                </div>
                <span className="w-16 shrink-0 text-right tabular-nums">{seconds.toFixed(1)} s</span>
              </div>
            ))}
          </div>
        )}

        {run.error && (
          <pre className="whitespace-pre-wrap rounded border border-line px-3 py-2 font-mono text-xs text-sev-high">
            {run.error}
          </pre>
        )}
      </div>
    </li>
  );
}

function stepSeconds(counts: Record<string, unknown>): [string, number][] {
  const raw = counts.step_seconds;
  if (!raw || typeof raw !== "object") return [];
  return Object.entries(raw as Record<string, unknown>).filter((e): e is [string, number] => typeof e[1] === "number");
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function utc(iso: string) {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}