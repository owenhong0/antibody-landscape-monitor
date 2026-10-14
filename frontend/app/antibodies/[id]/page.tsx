import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import {
  apiGet,
  ApiError,
  type AntibodyDetail,
  type Chain,
  type Liability,
  type Severity,
  type Trial,
} from "@/lib/api";

type Props = { params: Promise<{ id: string }> };

const SEVERITY_RANK: Record<Severity, number> = { high: 3, medium: 2, low: 1 };
const TRIALS_SHOWN = 15;

// Same pattern as the landscape: static shell, request-dependent part streams in.
export default function AntibodyPage({ params }: Props) {
  return (
    <Suspense fallback={<div className="h-96 animate-pulse rounded-lg border border-line bg-panel" />}>
      <AntibodyView params={params} />
    </Suspense>
  );
}

async function AntibodyView({ params }: Props) {
  const { id } = await params;
  let ab: AntibodyDetail;
  try {
    ab = await apiGet<AntibodyDetail>(`/api/antibodies/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  return (
    <div className="space-y-6">
      <Header ab={ab} />
      <Stats ab={ab} />

      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold">Chains and liabilities</h2>
          <Legend />
        </div>
        {ab.chains.length === 0 ? (
          <Empty>No sequence data for this antibody in Thera-SAbDab.</Empty>
        ) : (
          ab.chains.map((chain, i) => (
            <ChainCard key={chain.id} chain={chain} duplicateOf={findDuplicate(ab.chains, i)} />
          ))
        )}
      </section>

      <Trials trials={ab.trials} />
    </div>
  );
}

// --- header & summary ----------------------------------------------------------

function Header({ ab }: { ab: AntibodyDetail }) {
  return (
    <div className="space-y-2">
      <Link href={`/?target=${encodeURIComponent(ab.targets[0] ?? ab.target)}`} className="text-sm text-muted hover:text-ink">
        ← {ab.targets[0] ?? ab.target} landscape
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{ab.display_name}</h1>
        {ab.status === "discontinued" && (
          <span className="rounded bg-warn-soft px-2 py-0.5 text-xs font-medium uppercase text-warn">discontinued</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
        {ab.targets.map((t) => (
          <Link key={t} href={`/?target=${encodeURIComponent(t)}`} className="rounded bg-accent-soft px-2 py-0.5 font-medium text-accent">
            {t}
          </Link>
        ))}
        <span>·</span>
        <span>{ab.format ?? "Unknown format"}</span>
        <span>·</span>
        <span className="text-ink">{stageLabel(ab.clinical_stage)}</span>
        {ab.source_url && (
          <>
            <span>·</span>
            <a href={ab.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
              Thera-SAbDab ↗
            </a>
          </>
        )}
      </div>
    </div>
  );
}

function Stats({ ab }: { ab: AntibodyDetail }) {
  const c = ab.liability_counts;
  const tiles = [
    { label: "Chains", value: ab.chains.length },
    { label: "Liabilities", value: c.total },
    { label: "High severity", value: c.high, color: "var(--color-sev-high)" },
    { label: "In CDR3", value: c.in_cdr3 },
    { label: "Trials", value: ab.trials.length },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border border-line bg-panel px-4 py-3">
          <div className="text-xs text-muted">{t.label}</div>
          <div className="text-2xl font-semibold tabular-nums" style={t.color && t.value > 0 ? { color: t.color } : undefined}>
            {t.value}
          </div>
        </div>
      ))}
    </div>
  );
}

// --- chains ---------------------------------------------------------------------

function chainName(c: Chain) {
  return `${c.chain_type}${c.chain_index}`;
}

/** Name of an earlier chain with the identical sequence (common-heavy-chain bispecifics). */
function findDuplicate(chains: Chain[], i: number): string | null {
  const earlier = chains.slice(0, i).find((c) => c.sequence === chains[i].sequence);
  return earlier ? chainName(earlier) : null;
}

function ChainCard({ chain, duplicateOf }: { chain: Chain; duplicateOf: string | null }) {
  const c = chain.liability_counts;
  return (
    <article className="rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2.5">
        <h3 className="font-semibold">
          {chainName(chain)}{" "}
          <span className="font-normal text-muted">{chain.chain_type === "H" ? "heavy" : "light"} · {chain.length} aa</span>
        </h3>
        {duplicateOf && (
          <span className="rounded border border-line px-1.5 py-0.5 text-xs text-muted">
            same sequence as {duplicateOf}
          </span>
        )}
        <span className="ml-auto text-sm text-muted">
          {chain.cdr3_sequence ? (
            <>
              CDR3 <span className="font-mono text-ink">{chain.cdr3_sequence}</span> ({chain.cdr3_len} aa,{" "}
              {chain.cdr3_start}–{chain.cdr3_end})
            </>
          ) : (
            "No CDR3 match"
          )}
          {" · "}
          {c.total} liabilities, <span className="text-ink">{c.in_cdr3} in CDR3</span>
        </span>
      </header>
      <div className="space-y-4 p-4">
        <SequenceView chain={chain} />
        {chain.liabilities.length > 0 && <LiabilityTable liabilities={chain.liabilities} />}
      </div>
    </article>
  );
}

function SequenceView({ chain }: { chain: Chain }) {
  // Each residue gets the worst liability whose motif covers it.
  const worst = new Map<number, Liability>();
  for (const li of chain.liabilities) {
    for (let p = li.position; p < li.position + li.motif.length; p++) {
      const cur = worst.get(p);
      if (!cur || SEVERITY_RANK[li.severity] > SEVERITY_RANK[cur.severity]) worst.set(p, li);
    }
  }
  const inCdr3 = (pos: number) =>
    chain.cdr3_start !== null && chain.cdr3_end !== null && pos >= chain.cdr3_start && pos <= chain.cdr3_end;

  const blocks: number[] = [];
  for (let i = 0; i < chain.sequence.length; i += 10) blocks.push(i);

  return (
    <div className="flex flex-wrap gap-x-3 gap-y-2 font-mono text-sm leading-6">
      {blocks.map((start) => (
        <div key={start}>
          <div className="text-[10px] leading-3 text-muted">{start + 1}</div>
          <div>
            {chain.sequence.slice(start, start + 10).split("").map((aa, j) => {
              const pos = start + j + 1;
              const li = worst.get(pos);
              const cdr3 = inCdr3(pos);
              return (
                <span
                  key={pos}
                  title={`${aa}${pos}${li ? ` · ${li.kind} ${li.motif} (${li.severity})` : ""}${cdr3 ? " · CDR3" : ""}`}
                  className={`${cdr3 ? "bg-cdr3" : ""} ${li ? "border-b-2 font-bold" : ""}`}
                  style={li ? { color: `var(--color-sev-${li.severity})`, borderBottomColor: `var(--color-sev-${li.severity})` } : undefined}
                >
                  {aa}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function LiabilityTable({ liabilities }: { liabilities: Liability[] }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs text-muted">
        <tr className="border-b border-line">
          <th className="py-1.5 font-medium">Position</th>
          <th className="py-1.5 font-medium">Motif</th>
          <th className="py-1.5 font-medium">Kind</th>
          <th className="py-1.5 font-medium">Severity</th>
          <th className="py-1.5 font-medium">Region</th>
        </tr>
      </thead>
      <tbody>
        {liabilities.map((li) => (
          <tr key={li.id} className={`border-b border-line last:border-0 ${li.in_cdr3 ? "bg-cdr3" : ""}`}>
            <td className="py-1.5 tabular-nums">{li.position}</td>
            <td className="py-1.5 font-mono">{li.motif}</td>
            <td className="py-1.5">{li.kind.replaceAll("_", " ")}</td>
            <td className="py-1.5">
              <span className="font-medium" style={{ color: `var(--color-sev-${li.severity})` }}>
                {li.severity}
              </span>
            </td>
            <td className="py-1.5">{li.in_cdr3 ? <span className="font-semibold">CDR3</span> : <span className="text-muted">framework</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
      <span className="flex items-center gap-1">
        <span className="inline-block h-3 w-4 rounded-sm bg-cdr3" /> CDR3
      </span>
      {(["high", "medium", "low"] as const).map((s) => (
        <span key={s} className="flex items-center gap-1">
          <span className="font-mono font-bold border-b-2" style={{ color: `var(--color-sev-${s})`, borderBottomColor: `var(--color-sev-${s})` }}>
            N
          </span>
          {s}
        </span>
      ))}
      <span>· hover a residue for details</span>
    </div>
  );
}

// --- trials ---------------------------------------------------------------------

function Trials({ trials }: { trials: Trial[] }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">
        Clinical trials <span className="text-base font-normal text-muted">{trials.length}</span>
      </h2>
      {trials.length === 0 ? (
        <Empty>
          No ClinicalTrials.gov trials matched this INN. Newer antibodies are often registered under a
          development code instead.
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-panel">
          <TrialTable trials={trials.slice(0, TRIALS_SHOWN)} />
          {trials.length > TRIALS_SHOWN && (
            <details className="border-t border-line">
              <summary className="cursor-pointer px-4 py-2 text-sm text-accent">
                Show {trials.length - TRIALS_SHOWN} more
              </summary>
              <TrialTable trials={trials.slice(TRIALS_SHOWN)} />
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function TrialTable({ trials }: { trials: Trial[] }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs text-muted">
        <tr className="border-b border-line">
          <th className="px-4 py-2 font-medium">NCT id</th>
          <th className="px-4 py-2 font-medium">Title</th>
          <th className="px-4 py-2 font-medium">Phase</th>
          <th className="px-4 py-2 font-medium">Status</th>
          <th className="px-4 py-2 font-medium">Sponsor</th>
          <th className="px-4 py-2 text-right font-medium">Enrollment</th>
          <th className="px-4 py-2 font-medium">Updated</th>
        </tr>
      </thead>
      <tbody>
        {trials.map((t) => (
          <tr key={t.id} className="border-b border-line last:border-0 align-top">
            <td className="whitespace-nowrap px-4 py-2 font-mono">
              {t.source_url ? (
                <a href={t.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  {t.nct_id}
                </a>
              ) : (
                t.nct_id
              )}
            </td>
            <td className="max-w-md px-4 py-2">{t.title ?? "—"}</td>
            <td className="whitespace-nowrap px-4 py-2">{formatPhase(t.phase)}</td>
            <td className="whitespace-nowrap px-4 py-2">{formatEnum(t.status)}</td>
            <td className="px-4 py-2 text-muted">{t.sponsor ?? "—"}</td>
            <td className="px-4 py-2 text-right tabular-nums">{t.enrollment?.toLocaleString("en-US") ?? "—"}</td>
            <td className="whitespace-nowrap px-4 py-2 tabular-nums text-muted">{t.last_update_date ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- helpers --------------------------------------------------------------------

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>;
}

function stageLabel(stage: string | null) {
  if (!stage) return "Unknown stage";
  return stage.replace("phase_", "Phase ").replace(/^\w/, (c) => c.toUpperCase());
}

function formatPhase(phase: string | null) {
  if (!phase) return "—";
  return phase.replace(/PHASE(\d)/g, "Phase $1").replace("|", "/").replace("EARLY_", "Early ");
}

function formatEnum(value: string | null) {
  if (!value) return "—";
  const s = value.replaceAll("_", " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}