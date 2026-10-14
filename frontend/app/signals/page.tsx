import Link from "next/link";
import { Suspense } from "react";
import { apiGet, type Page, type Signal, type SignalKind } from "@/lib/api";
import { MarkSeenButton } from "./MarkSeenButton";

const PAGE_SIZE = 25;

const VIEWS = [
  { key: "latest", label: "Latest run" },
  { key: "unseen", label: "Unseen" },
  { key: "all", label: "All" },
] as const;
type View = (typeof VIEWS)[number]["key"];

const KINDS: Record<SignalKind, { label: string; color: string }> = {
  NEW_ANTIBODY: { label: "New antibody", color: "#10b981" },
  ANTIBODY_STAGE_CHANGED: { label: "Stage change", color: "#3b82f6" },
  NEW_TRIAL: { label: "New trial", color: "#8b5cf6" },
  TRIAL_STATUS_CHANGED: { label: "Trial status", color: "#f59e0b" },
  TRIAL_PHASE_CHANGED: { label: "Trial phase", color: "#ec4899" },
};

type SearchParams = { view?: string; kind?: string; offset?: string };
type Props = { searchParams: Promise<SearchParams> };

export default function SignalsPage({ searchParams }: Props) {
  return (
    <Suspense fallback={<div className="h-96 animate-pulse rounded-lg border border-line bg-panel" />}>
      <SignalsView searchParams={searchParams} />
    </Suspense>
  );
}

async function SignalsView({ searchParams }: Props) {
  const sp = await searchParams;
  const view: View = VIEWS.some((v) => v.key === sp.view) ? (sp.view as View) : "latest";
  const kind = sp.kind && Object.hasOwn(KINDS, sp.kind) ? (sp.kind as SignalKind) : undefined;
  const offset = Math.max(0, Number(sp.offset) || 0);

  const q = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (view === "latest") q.set("run_id", "latest");
  if (view === "unseen") q.set("seen", "false");
  if (kind) q.set("kind", kind);
  const page = await apiGet<Page<Signal>>(`/api/signals?${q}`);

  const href = (next: { view?: View; kind?: SignalKind | null; offset?: number }) => {
    const p = new URLSearchParams();
    const v = next.view ?? view;
    const k = next.kind === undefined ? kind : next.kind;
    if (v !== "latest") p.set("view", v);
    if (k) p.set("kind", k);
    if (next.offset) p.set("offset", String(next.offset));
    const s = p.toString();
    return s ? `/signals?${s}` : "/signals";
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">
          Signals <span className="text-base font-normal text-muted">{page.total}</span>
        </h1>
        <p className="mt-1 text-sm text-muted">
          What changed between pipeline runs: new antibodies, stage moves and trial updates.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {VIEWS.map((v) => (
          <Pill key={v.key} href={href({ view: v.key, offset: 0 })} active={v.key === view}>
            {v.label}
          </Pill>
        ))}
        <span className="mx-2 h-5 w-px bg-line" />
        <Pill href={href({ kind: null, offset: 0 })} active={!kind}>
          All kinds
        </Pill>
        {(Object.keys(KINDS) as SignalKind[]).map((k) => (
          <Pill key={k} href={href({ kind: k, offset: 0 })} active={k === kind}>
            <Dot color={KINDS[k].color} />
            {KINDS[k].label}
          </Pill>
        ))}
      </div>

      {page.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
          {view === "latest" ? "No changes in the latest pipeline run." : "No signals match these filters."}
        </p>
      ) : (
        <ul className="space-y-2">
          {page.items.map((s) => (
            <SignalRow key={s.id} signal={s} />
          ))}
        </ul>
      )}

      <Pagination total={page.total} offset={offset} href={(o) => href({ offset: o })} />
    </div>
  );
}

function SignalRow({ signal: s }: { signal: Signal }) {
  const kind = KINDS[s.kind];
  return (
    <li
      className={`flex items-start gap-4 rounded-lg border border-line bg-panel px-4 py-3 ${
        s.seen ? "opacity-60" : "border-l-4 border-l-accent"
      }`}
    >
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 rounded border border-line px-1.5 py-0.5 text-xs font-medium">
            <Dot color={kind.color} />
            {kind.label}
          </span>
          <span className="font-medium">{s.title}</span>
        </div>

        {(s.old_value || s.new_value) && (
          <div className="font-mono text-sm">
            {s.old_value ? (
              <>
                <span className="text-muted line-through">{s.old_value}</span>
                <span className="mx-2 text-muted">→</span>
              </>
            ) : null}
            <span className="font-semibold">{s.new_value ?? "—"}</span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          {s.entity ? (
            s.entity.antibodies.map((ab) => (
              <Link key={ab.id} href={`/antibodies/${ab.id}`} className="rounded bg-accent-soft px-1.5 py-0.5 font-medium text-accent">
                {ab.display_name}
              </Link>
            ))
          ) : (
            <span>{s.entity_type} {s.entity_id} no longer exists</span>
          )}
          {s.source_url && (
            <a href={s.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
              Source ↗
            </a>
          )}
          <span>·</span>
          <span className="tabular-nums">{utc(s.created_at)}</span>
          <span>·</span>
          <span>run {s.run_id}</span>
        </div>
      </div>

      <div className="shrink-0">
        {s.seen ? (
          <span className="text-xs text-muted">✓ seen {s.seen_at ? utc(s.seen_at) : ""}</span>
        ) : (
          <MarkSeenButton id={s.id} />
        )}
      </div>
    </li>
  );
}

function Pagination({ total, offset, href }: { total: number; offset: number; href: (o: number) => string }) {
  if (total <= PAGE_SIZE) return null;
  const end = Math.min(offset + PAGE_SIZE, total);
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted tabular-nums">
        {offset + 1}–{end} of {total}
      </span>
      <div className="flex gap-2">
        {offset > 0 && <Pill href={href(Math.max(0, offset - PAGE_SIZE))}>← Newer</Pill>}
        {end < total && <Pill href={href(offset + PAGE_SIZE)}>Older →</Pill>}
      </div>
    </div>
  );
}

function Pill({ href, active = false, children }: { href: string; active?: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm ${
        active ? "border-accent bg-accent text-on-accent" : "border-line bg-panel text-ink hover:border-accent"
      }`}
    >
      {children}
    </Link>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />;
}

/** "2026-10-13T09:01:05+00:00" -> "2026-10-13 09:01 UTC" (same on server and client). */
function utc(iso: string) {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}