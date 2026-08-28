/**
 * D7.4 Episode detail — the agent-transparency centerpiece. Everything
 * renders from /api/episodes/{id} verbatim: a header card (full mono ids
 * with copy buttons, cohort/state pills, halt time, plan amount,
 * attempts, recovered total with honest zeros), the policy-evaluation
 * summary when the ledger carries one, the vertical agent timeline, and
 * — only when a PENDING approval exists — the approve/reject block wired
 * to POST /api/approvals/{id}/decide: refetch on success, the server's
 * own 409 detail inline when the decision was already taken. Unknown ids
 * get an honest not-found state, never a fake timeline.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AgentTimeline } from "@/components/AgentTimeline";
import { EpisodeStatePill, StatusPill } from "@/components/StatusPill";
import {
  ApiError,
  postDecide,
  useApi,
  type EpisodeDetailResponse,
  type EpisodeRow,
  type LedgerRow,
  type PendingApproval,
} from "@/lib/api";
import { formatInr, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

// Frozen system constants (frontend mirrors, single sources live server-
// side): app.actions.recovery_link.RECOVERY_PLAN_PAISE and
// app.policy.engine.HUMAN_GATE_THRESHOLD_PAISE — the threshold math the
// gate card shows is computed from these, never invented per-request.
const PLAN_PRICE_PAISE = 49900;
const GATE_THRESHOLD_PAISE = 50000;

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard unavailable (permissions/insecure context) — the id
          // is fully visible text anyway; the button is a convenience.
        }
      }}
      className="rounded-button border border-border-normal px-12 py-2 text-xs font-medium text-text-subtle hover:border-border-hover hover:text-text-normal"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function IdRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap items-center gap-8">
      <span className="w-104 shrink-0 text-xs font-medium uppercase text-text-muted">
        {label}
      </span>
      <span className="min-w-0 break-all font-mono text-sm text-text-normal" title={value}>
        {value}
      </span>
      <CopyButton value={value} />
    </div>
  );
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex flex-col gap-4">
      <dt className="text-xs font-medium uppercase text-text-muted">{label}</dt>
      <dd className="tnum break-all font-mono text-sm text-text-normal" title={title}>
        {value}
      </dd>
    </div>
  );
}

function HeaderCard({ episode }: { episode: EpisodeRow }) {
  const recovered = episode.recovered_paise;
  return (
    <section className="flex flex-col gap-16 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
      <div className="flex flex-wrap items-center gap-8">
        <EpisodeStatePill state={episode.state} />
        {episode.cohort !== null && <StatusPill tone="neutral">{episode.cohort}</StatusPill>}
        {episode.void_reason !== null && (
          <StatusPill tone="negative">{`void · ${episode.void_reason}`}</StatusPill>
        )}
      </div>
      <div className="flex flex-col gap-8">
        <IdRow label="Episode" value={episode.id} />
        <IdRow label="Subscription" value={episode.subscription_id} />
      </div>
      <dl className="grid grid-cols-4 gap-16">
        <Fact label="Halted" value={timeAgo(episode.halt_ts_utc)} title={episode.halt_ts_utc} />
        <Fact label="Plan amount" value={formatInr(PLAN_PRICE_PAISE)} title="fixed ₹499 plan (RECOVERY_PLAN_PAISE)" />
        <Fact label="Attempts" value={`${episode.attempt_count}`} title="outreach sends this cycle (cap 3)" />
        <Fact
          label="Recovered"
          value={recovered > 0 ? formatInr(recovered) : "—"}
          title={recovered > 0 ? "summed over this episode's ledger window" : "no recovery yet"}
        />
      </dl>
    </section>
  );
}

/**
 * Policy-evaluation summary: the engine writes its verdict into the
 * ledger, so the card shows exactly what was recorded — the verdict row
 * (positive dot when the rules passed, notice when blocked/gated) plus
 * the reason and the structured detail lines. Nothing per-rule is
 * invented: only the FIRST failing rule is ever written server-side.
 */
function PolicyCard({ timeline }: { timeline: LedgerRow[] }) {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const policyEval = timeline[i].policy_eval;
    if (policyEval === null || typeof policyEval !== "object" || Array.isArray(policyEval)) {
      continue;
    }
    const pe = policyEval as Record<string, unknown>;
    const passed = pe.ok === true;
    const blocked = pe.ok === false;
    const verdict =
      typeof pe.reason === "string"
        ? pe.reason
        : typeof pe.decision === "string"
          ? pe.decision
          : "policy_eval recorded";
    const details: string[] = [];
    if (typeof pe.from_state === "string" || typeof pe.to_state === "string") {
      details.push(`${pe.from_state ?? "·"} → ${pe.to_state ?? "·"}`);
    }
    if (typeof pe.tier === "number") details.push(`tier ${pe.tier}`);
    if (typeof pe.rationale === "string") details.push(pe.rationale);
    if (typeof pe.details === "object" && pe.details !== null) {
      details.push(JSON.stringify(pe.details));
    }
    if (typeof pe.approval_id === "string") details.push(`approval ${pe.approval_id}`);
    return (
      <section className="flex flex-col gap-8 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
        <h2 className="text-xs font-medium uppercase text-text-muted">Policy evaluation</h2>
        <div className="flex items-start gap-8">
          <span
            aria-hidden
            className={cn(
              "mt-4 h-8 w-8 shrink-0 rounded-pill",
              passed ? "bg-positive-solid" : blocked ? "bg-notice-solid" : "bg-border-normal",
            )}
          />
          <div className="flex min-w-0 flex-col gap-4">
            <p className="font-mono text-sm text-text-normal">{verdict}</p>
            {details.map((line) => (
              <p key={line} className="break-all font-mono text-xs text-text-muted">
                {line}
              </p>
            ))}
          </div>
        </div>
      </section>
    );
  }
  return null;
}

const REASON_NOTES: Record<string, string> = {
  tier3_escalation: "tier-3 risk — always escalated to a human, whatever the model recommended",
  amount_over_threshold: "amount strictly above the ₹500 human-gate threshold",
};

function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: PendingApproval;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "reject") {
    setBusy(decision);
    setError(null);
    try {
      await postDecide(approval.id, decision);
      onDecided();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Already decided — this approval was settled elsewhere; the ledger below holds the recorded outcome."
          : err instanceof Error
            ? err.message
            : "request failed",
      );
    } finally {
      setBusy(null);
    }
  }

  const underThreshold = PLAN_PRICE_PAISE <= GATE_THRESHOLD_PAISE;
  return (
    <section className="flex flex-col gap-12 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
      <div className="flex flex-wrap items-center gap-8">
        <StatusPill tone="notice" dot>
          GATED — awaiting human decision
        </StatusPill>
        <span className="text-sm text-text-muted" title={approval.created_ts_utc}>
          queued {timeAgo(approval.created_ts_utc)}
        </span>
      </div>

      <div className="flex flex-col gap-4">
        <p className="text-sm text-text-subtle">
          Gate reason: <span className="font-mono">{approval.reason}</span>
          {REASON_NOTES[approval.reason] !== undefined && (
            <span className="text-text-muted"> — {REASON_NOTES[approval.reason]}</span>
          )}
        </p>
        <p className="tnum font-mono text-xs text-text-muted">
          threshold math: {formatInr(PLAN_PRICE_PAISE)} (plan) {underThreshold ? "≤" : ">"}{" "}
          {formatInr(GATE_THRESHOLD_PAISE)} (gate) —{" "}
          {underThreshold
            ? "the amount alone would not gate; outreach above ₹500.00 is what requires a human"
            : "the amount itself crosses the human-gate threshold"}
        </p>
      </div>

      {error !== null && (
        <p role="alert" className="rounded-card bg-negative-bg p-12 text-sm font-medium text-negative-text">
          {error}
        </p>
      )}

      <div className="flex gap-8">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("approve")}
          className="h-control-md rounded-button bg-primary px-20 text-sm font-medium text-surface hover:bg-primary-hover disabled:opacity-50"
        >
          {busy === "approve" ? "Recording…" : "Approve"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("reject")}
          className="h-control-md rounded-button border border-negative-solid bg-surface px-20 text-sm font-medium text-negative-text hover:bg-negative-bg disabled:opacity-50"
        >
          {busy === "reject" ? "Recording…" : "Reject"}
        </button>
      </div>
    </section>
  );
}

export function EpisodeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useApi<EpisodeDetailResponse>(`/api/episodes/${id ?? ""}`);

  return (
    <div className="flex flex-col gap-24">
      <Link to="/episodes" className="text-sm font-medium text-primary hover:text-primary-hover">
        ← All episodes
      </Link>

      {detail.error !== null ? (
        <div className="flex flex-col gap-8 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
          <p className="font-display text-lg font-semibold text-text-normal">Episode not found</p>
          <p className="text-sm text-text-muted">
            No episode with id <span className="break-all font-mono">{id}</span> — the server
            said: {detail.error}
          </p>
        </div>
      ) : detail.data === null ? (
        <p className="text-sm text-text-muted">Loading episode…</p>
      ) : (
        <>
          <HeaderCard episode={detail.data.episode} />
          {detail.data.pending_approval !== null && (
            <ApprovalCard
              approval={detail.data.pending_approval}
              onDecided={detail.refetch}
            />
          )}
          <PolicyCard timeline={detail.data.timeline} />
          <section className="flex flex-col gap-16">
            <h2 className="text-xs font-medium uppercase text-text-muted">Agent timeline</h2>
            <AgentTimeline rows={detail.data.timeline} />
          </section>
        </>
      )}
    </div>
  );
}
