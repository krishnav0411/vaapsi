/**
 * Typed fetch layer for the FastAPI JSON API (app/dashboard/api.py).
 *
 * Every shape here is read from api.py and the helpers it delegates to
 * (metrics.py triples, episodes SELECT *, FastAPI HTTPException error
 * envelopes). Error bodies surface the
 * server's own `detail` verbatim so honesty survives the transport
 * (a 400 kill-confirm shows the server's words, not a generic string).
 */

import { useCallback, useEffect, useState } from "react";

export type Mode = "NORMAL" | "DEGRADED" | "KILLED";

export type Cohort = "TREATMENT" | "CONTROL";

export type EpisodeState =
  | "NEW"
  | "DIAGNOSED"
  | "SCORED"
  | "GATED"
  | "SENT"
  | "VERIFIED"
  | "CLOSED"
  | "VOIDED";

/** metrics.py's universal (value, n, note) triple, serialized. */
export interface MetricTuple<T> {
  value: T;
  n: number;
  note: string;
}

export interface OverviewStats {
  recovered_paise: number;
  recovered_paise_n: number;
  recovered_paise_note: string;
  recovery_rate_treatment: MetricTuple<number | null>;
  recovery_rate_control: MetricTuple<number | null>;
  open_episodes: number;
}

export interface OverviewResponse {
  stats: OverviewStats;
  cohorts: Partial<Record<Cohort, number>>;
  mode: Mode;
}

/**
 * Episodes row: `SELECT e.*` plus the pending-approval EXISTS flag plus
 * the Stage C per-episode recovered total (SUM over the same ledger
 * window the detail timeline draws — joined server-side in api.py).
 */
export interface EpisodeRow {
  id: string;
  subscription_id: string;
  cohort: Cohort | null;
  state: EpisodeState;
  halt_ts_utc: string;
  attempt_count: number;
  last_action_ts_utc: string | null;
  void_reason: "charged" | "cancelled" | null;
  created_ts_utc: string;
  updated_ts_utc: string;
  pending_approval: 0 | 1;
  recovered_paise: number;
}

export interface PendingApproval {
  id: string;
  episode_id: string;
  subscription_id: string;
  reason: string;
  status: "PENDING" | "APPROVED" | "REJECTED";
  created_ts_utc: string;
}

export interface LedgerRow {
  seq: number;
  ts_utc: string;
  subscription_id: string;
  trigger_event: string;
  policy_eval: unknown;
  score: number | null;
  human_gate: 0 | 1;
  rzp_call: string | null;
  outcome: string;
  recovered_paise: number;
  mode: string;
}

export interface EpisodeDetailResponse {
  episode: EpisodeRow;
  timeline: LedgerRow[];
  pending_approval: PendingApproval | null;
}

export interface Metric {
  name: string;
  value: number | null;
  n: number;
  note: string;
}

export interface ModeResponse {
  mode: Mode;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body — the status line is the honest fallback.
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function getOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  return fetchJson<OverviewResponse>("/api/overview", { signal });
}

export function getEpisodes(
  params: { state?: string; cohort?: string } = {},
  signal?: AbortSignal,
): Promise<EpisodeRow[]> {
  const qs = new URLSearchParams();
  if (params.state) qs.set("state", params.state);
  if (params.cohort) qs.set("cohort", params.cohort);
  const suffix = qs.size > 0 ? `?${qs.toString()}` : "";
  return fetchJson<EpisodeRow[]>(`/api/episodes${suffix}`, { signal });
}

export function getEpisodeDetail(id: string, signal?: AbortSignal): Promise<EpisodeDetailResponse> {
  return fetchJson<EpisodeDetailResponse>(`/api/episodes/${encodeURIComponent(id)}`, { signal });
}

export function getMetrics(signal?: AbortSignal): Promise<Metric[]> {
  return fetchJson<Metric[]>("/api/metrics", { signal });
}

export function getMode(signal?: AbortSignal): Promise<ModeResponse> {
  return fetchJson<ModeResponse>("/api/mode", { signal });
}

export function postKill(confirm: string): Promise<ModeResponse> {
  return fetchJson<ModeResponse>("/api/kill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm }),
  });
}

export function postDecide(
  approvalId: string,
  decision: "approve" | "reject",
  note = "",
): Promise<unknown> {
  return fetchJson<unknown>(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note }),
  });
}

// ── D8 ledger explorer (shapes read from api.py + tests/test_ledger_api.py) ──

/** One block-explorer row: hashes truncated SERVER-SIDE (prev 12, hash 16). */
export interface LedgerListItem {
  seq: number;
  ts_utc: string;
  trigger_event: string;
  actor: "agent" | "human";
  outcome: string;
  subscription_id: string;
  prev_hash: string;
  hash: string;
}

export interface LedgerListResponse {
  rows: LedgerListItem[];
  total: number;
  chain_valid: boolean;
}

/** One FULL ledger row (GET /api/ledger/{seq}): every column, 64-char hashes. */
export interface LedgerRowDetail {
  seq: number;
  action_id: string;
  ts_utc: string;
  subscription_id: string;
  trigger_event: string;
  policy_eval: unknown;
  score: number | null;
  features: unknown;
  llm_request_hash: string | null;
  llm_output_raw: unknown;
  llm_model: string | null;
  human_gate: boolean;
  rzp_call: unknown;
  outcome: string;
  recovered_paise: number;
  mode: string;
  prev_hash: string;
  row_hash: string;
  prev_seq: number | null;
  canonical_json: string;
}

export interface LedgerVerifyResponse {
  valid: boolean;
  rows: number;
  broken_seq: number | null;
  detail: string;
}

export interface TamperDemoResponse {
  verdict: "tamper_detected" | "empty_ledger";
  broken_seq: number | null;
  field: string | null;
  expected_value: number | null;
  found_value: number | null;
  stored_hash: string | null;
  recomputed_hash: string | null;
  verify_detail: string;
  rows: number;
  original_store_chain_valid: boolean;
  original_rows: number;
}

// ── D8 approvals inbox ────────────────────────────────────────────────────

export interface ApprovalSummary {
  id: string;
  episode_id: string;
  subscription_id: string;
  reason: string;
  status: string;
  created_ts_utc: string;
  episode_state: string;
  attempt_count: number;
  tier: number | null;
  amount_paise: number;
  threshold_paise: number;
  exceeds_threshold: boolean;
  over_by_paise: number;
  proposed_action: string;
}

export interface ApprovalsPendingResponse {
  approvals: ApprovalSummary[];
}

export interface ApprovalDetailResponse {
  approval: ApprovalSummary;
  episode: EpisodeRow;
  timeline: LedgerRow[];
}

// ── D8 drills console ─────────────────────────────────────────────────────

export interface DrillRunResult {
  drill_id: string;
  passed: boolean;
  summary: string;
  evidence: Record<string, unknown>;
  ran_ts_utc: string;
  duration_ms: number;
}

export interface DrillInfo {
  drill_id: string;
  title: string;
  description: string;
  last_run: DrillRunResult | null;
}

export interface DrillsResponse {
  drills: DrillInfo[];
}

export function getLedger(
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<LedgerListResponse> {
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.set("limit", `${params.limit}`);
  if (params.offset !== undefined) qs.set("offset", `${params.offset}`);
  return fetchJson<LedgerListResponse>(`/api/ledger?${qs.toString()}`, { signal });
}

export function getLedgerRow(seq: number, signal?: AbortSignal): Promise<LedgerRowDetail> {
  return fetchJson<LedgerRowDetail>(`/api/ledger/${seq}`, { signal });
}

export function verifyLedger(signal?: AbortSignal): Promise<LedgerVerifyResponse> {
  return fetchJson<LedgerVerifyResponse>("/api/ledger/verify", { signal });
}

export function runTamperDemo(): Promise<TamperDemoResponse> {
  return fetchJson<TamperDemoResponse>("/api/ledger/tamper-demo", { method: "POST" });
}

export function getPendingApprovals(signal?: AbortSignal): Promise<ApprovalsPendingResponse> {
  return fetchJson<ApprovalsPendingResponse>("/api/approvals/pending", { signal });
}

export function getApprovalDetail(
  approvalId: string,
  signal?: AbortSignal,
): Promise<ApprovalDetailResponse> {
  return fetchJson<ApprovalDetailResponse>(
    `/api/approvals/${encodeURIComponent(approvalId)}/detail`,
    { signal },
  );
}

export function getDrills(signal?: AbortSignal): Promise<DrillsResponse> {
  return fetchJson<DrillsResponse>("/api/drills", { signal });
}

export function runDrill(drillId: string): Promise<DrillRunResult> {
  return fetchJson<DrillRunResult>(`/api/drills/${encodeURIComponent(drillId)}/run`, {
    method: "POST",
  });
}

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * One GET per path change, abortable, with honest loading/error states —
 * a consumer shows data, the error text, or nothing-yet; never a
 * fabricated intermediate. refetch re-runs the GET (the kill dialog's
 * success path uses it to flip every mode surface at once).
 */
export function useApi<T>(path: string): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    setLoading(true);
    fetchJson<T>(path, { signal: controller.signal })
      .then((result) => {
        setData(result);
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(err instanceof Error ? err.message : "request failed");
        setLoading(false);
      });
    return () => controller.abort();
  }, [path, nonce]);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, refetch };
}
