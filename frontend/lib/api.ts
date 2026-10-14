// Typed client for the Flask API. Shapes mirror docs/api.md.
// Used by Server Components (apiGet) and by the browser (apiPost from MarkSeenButton).
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5001";

export const TARGETS = ["PD-1", "PD-L1", "HER2", "CD20", "IL-23"] as const;
export type Target = (typeof TARGETS)[number];

// --- landscape -------------------------------------------------------------------

export type AntibodySummary = {
  id: number;
  inn_name: string;
  display_name: string;
  targets: string[];
  format: string | null;
  clinical_stage: string | null;
  stage_rank: number | null;
  status: string | null;
};

export type Stage = { stage_rank: number | null; stage: string; label: string };
export type LandscapeColumn = Stage & { count: number; antibodies: AntibodySummary[] };
export type Landscape = { target: string; total: number; columns: LandscapeColumn[] };
export type Overview = {
  stages: Stage[];
  targets: { target: string; total: number; counts: Record<string, number> }[];
  antibodies_total: number;
};

// --- antibody detail -------------------------------------------------------------

export type Severity = "high" | "medium" | "low";

export type Liability = {
  id: number;
  kind: string;
  motif: string;
  position: number; // 1-based
  in_cdr3: boolean;
  severity: Severity;
};

export type LiabilityCounts = Record<Severity | "in_cdr3" | "total", number>;

export type Chain = {
  id: number;
  chain_type: string; // "H" | "L"
  chain_index: number;
  length: number;
  sequence: string;
  cdr3_sequence: string | null;
  cdr3_len: number | null;
  cdr3_start: number | null; // 1-based, inclusive
  cdr3_end: number | null;
  liability_counts: LiabilityCounts;
  liabilities: Liability[];
};

export type Trial = {
  id: number;
  nct_id: string;
  title: string | null;
  sponsor: string | null;
  status: string | null;
  phase: string | null;
  enrollment: number | null;
  last_update_date: string | null;
  source_url: string | null;
};

export type AntibodyDetail = AntibodySummary & {
  target: string;
  source_url: string | null;
  created_at: string;
  updated_at: string;
  liability_counts: LiabilityCounts;
  chains: Chain[];
  trials: Trial[];
};

// --- signals & runs --------------------------------------------------------------

export type SignalKind =
  | "NEW_ANTIBODY"
  | "ANTIBODY_STAGE_CHANGED"
  | "NEW_TRIAL"
  | "TRIAL_STATUS_CHANGED"
  | "TRIAL_PHASE_CHANGED";

export type Signal = {
  id: number;
  run_id: number;
  kind: SignalKind;
  title: string;
  old_value: string | null;
  new_value: string | null;
  source_url: string | null;
  entity_type: "antibody" | "trial";
  entity_id: number;
  entity: { label: string; antibodies: { id: number; display_name: string }[] } | null;
  seen: boolean;
  seen_at: string | null;
  created_at: string;
};

export type RunStatus = "running" | "succeeded" | "failed" | "partial";

export type Run = {
  id: number;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  counts: Record<string, unknown>; // JSONB: numbers, plus step_seconds: {step: seconds}
  raw_location: string | null;
  error: string | null;
};

export type Page<T> = { items: T[]; total: number; limit: number; offset?: number };

// --- requests --------------------------------------------------------------------

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.message ?? `${res.status} ${res.statusText}`);
  }
  return body as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPost<T>(path: string): Promise<T> {
  return request<T>(path, { method: "POST" });
}