/**
 * Shared fixtures for the component test suite — minimal, honest shapes
 * matching lib/api's types. Tests mock "@/lib/api" at the module
 * boundary; nothing here ever touches the network.
 */

import type {
  ApprovalDetailResponse,
  ApprovalSummary,
  DrillInfo,
  DrillRunResult,
  EpisodeDetailResponse,
  EpisodeRow,
  LedgerListItem,
  LedgerListResponse,
  LedgerRowDetail,
  LedgerVerifyResponse,
} from "@/lib/api";

export function makeEpisode(overrides: Partial<EpisodeRow> = {}): EpisodeRow {
  return {
    id: "ep_01",
    subscription_id: "sub_01",
    cohort: "TREATMENT",
    state: "SENT",
    halt_ts_utc: "2026-08-20T10:00:00Z",
    attempt_count: 1,
    last_action_ts_utc: null,
    void_reason: null,
    created_ts_utc: "2026-08-20T10:00:00Z",
    updated_ts_utc: "2026-08-20T10:05:00Z",
    pending_approval: 0,
    recovered_paise: 0,
    ...overrides,
  };
}

export function makeEpisodeDetail(
  overrides: Partial<EpisodeDetailResponse> = {},
): EpisodeDetailResponse {
  return {
    episode: makeEpisode({ id: "ep_ctx", subscription_id: "sub_ctx" }),
    timeline: [],
    pending_approval: null,
    ...overrides,
  };
}

export function makeLedgerListItem(
  overrides: Partial<LedgerListItem> = {},
): LedgerListItem {
  return {
    seq: 1,
    ts_utc: "2026-08-20T10:00:00Z",
    trigger_event: "subscription.halted",
    actor: "agent",
    outcome: "EPISODE_CREATED",
    subscription_id: "sub_01",
    prev_hash: "prev0123456789",
    hash: "rowhash0123456789",
    ...overrides,
  };
}

export function makeLedgerListResponse(
  overrides: Partial<LedgerListResponse> = {},
): LedgerListResponse {
  return {
    rows: [
      makeLedgerListItem(),
      makeLedgerListItem({ seq: 2, subscription_id: "sub_02", outcome: "EPISODE_SENT" }),
    ],
    total: 2,
    chain_valid: true,
    ...overrides,
  };
}

export function makeLedgerVerifyResponse(
  overrides: Partial<LedgerVerifyResponse> = {},
): LedgerVerifyResponse {
  return {
    valid: true,
    rows: 2,
    broken_seq: null,
    detail: "chain ok",
    ...overrides,
  };
}

export function makeLedgerRowDetail(
  overrides: Partial<LedgerRowDetail> = {},
): LedgerRowDetail {
  return {
    seq: 1,
    action_id: "act_01",
    ts_utc: "2026-08-20T10:00:00Z",
    subscription_id: "sub_01",
    trigger_event: "subscription.halted",
    policy_eval: null,
    score: null,
    features: null,
    llm_request_hash: null,
    llm_output_raw: null,
    llm_model: null,
    human_gate: false,
    rzp_call: null,
    outcome: "EPISODE_CREATED",
    recovered_paise: 0,
    mode: "NORMAL",
    prev_hash: "a".repeat(64),
    row_hash: "b".repeat(64),
    prev_seq: null,
    canonical_json: '{"seq":1}',
    ...overrides,
  };
}

export function makeDrill(overrides: Partial<DrillInfo> = {}): DrillInfo {
  return {
    drill_id: "replay_storm",
    title: "Replay storm",
    description: "Replays a webhook storm against a throwaway store.",
    last_run: null,
    ...overrides,
  };
}

export function makeDrillResult(
  overrides: Partial<DrillRunResult> = {},
): DrillRunResult {
  return {
    drill_id: "replay_storm",
    passed: true,
    summary: "42 webhooks replayed, chain stayed valid",
    evidence: {},
    ran_ts_utc: "2026-08-20T10:00:00Z",
    duration_ms: 1234,
    ...overrides,
  };
}

export function makeApproval(
  overrides: Partial<ApprovalSummary> = {},
): ApprovalSummary {
  return {
    id: "ap_1",
    episode_id: "ep_01",
    subscription_id: "sub_01",
    reason: "tier3_escalation",
    status: "PENDING",
    created_ts_utc: "2026-08-20T10:00:00Z",
    episode_state: "GATED",
    attempt_count: 0,
    tier: 3,
    amount_paise: 49900,
    threshold_paise: 50000,
    exceeds_threshold: false,
    over_by_paise: 0,
    proposed_action: "send recovery link",
    ...overrides,
  };
}

export function makeApprovalDetail(
  overrides: Partial<ApprovalDetailResponse> = {},
): ApprovalDetailResponse {
  return {
    approval: makeApproval(),
    episode: makeEpisode({ state: "GATED" }),
    timeline: [],
    ...overrides,
  };
}
