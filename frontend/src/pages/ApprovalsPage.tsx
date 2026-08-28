/**
 * D8 Approvals inbox — every PENDING human-gate decision, oldest first.
 * A queue card opens the detail panel: proposed action vs current state
 * (two-column comparison straight off the API's summary), the threshold
 * math in integer paise, and the Approve/Reject ritual over POST
 * /api/approvals/{id}/decide — the typed reason is required for a
 * reject and travels into the ledger as the decision note. A/R keys act
 * only while a detail is open and no modal owns the keyboard. After a
 * decision the list refetches and an inline confirmation replaces the
 * panel. Empty queue renders a calm centered card, never an error look.
 */

import { useEffect, useState } from "react";
import { CheckCircle2, X } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { EpisodeStatePill, StatusPill } from "@/components/StatusPill";
import { CardSkeleton } from "@/components/Skeleton";
import { useDelayedFlag } from "@/hooks/useDelayedFlag";
import {
  ApiError,
  postDecide,
  useApi,
  type ApprovalDetailResponse,
  type ApprovalSummary,
  type ApprovalsPendingResponse,
} from "@/lib/api";
import { formatInr, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const REASON_NOTES: Record<string, string> = {
  tier3_escalation: "tier-3 risk — always escalated to a human, whatever the model recommended",
  amount_over_threshold: "amount strictly above the ₹500 human-gate threshold",
};

function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-card bg-negative-bg p-16 text-sm font-medium text-negative-text"
    >
      {message}
    </div>
  );
}

function ApprovalDetail({
  approvalId,
  onClose,
  onDecided,
}: {
  approvalId: string;
  onClose: () => void;
  onDecided: (message: string) => void;
}) {
  const detail = useApi<ApprovalDetailResponse>(
    `/api/approvals/${encodeURIComponent(approvalId)}/detail`,
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "reject") {
    if (busy !== null) return;
    if (decision === "reject" && note.trim() === "") {
      setError("A typed reason is required to reject — write why, then reject again. The reason is recorded in the ledger.");
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      await postDecide(approvalId, decision, note.trim());
      onDecided(
        `${decision === "approve" ? "Approved" : "Rejected"} ${approvalId} — recorded in the ledger.`,
      );
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Already decided — this approval was settled elsewhere; the list above holds the live queue."
          : err instanceof Error
            ? err.message
            : "decide request failed",
      );
      setBusy(null);
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target !== null &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (document.querySelector("[role='dialog']") !== null) return;
      if (busy !== null) return;
      if (event.key.toLowerCase() === "a") {
        event.preventDefault();
        void decide("approve");
      } else if (event.key.toLowerCase() === "r" && note.trim() !== "") {
        event.preventDefault();
        void decide("reject");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (detail.error !== null) {
    return (
      <section className="flex flex-col gap-8 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
        <div className="flex items-center justify-between">
          <p className="font-display text-lg font-semibold text-text-normal">
            Approval detail unavailable
          </p>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close approval detail"
            className="flex h-control-sm w-control-sm items-center justify-center rounded-button border border-border-normal text-text-subtle hover:border-border-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="h-16 w-16" aria-hidden />
          </button>
        </div>
        <p className="text-sm text-text-muted">{detail.error}</p>
      </section>
    );
  }
  if (detail.data === null) {
    return <p className="text-sm text-text-muted">Loading approval detail…</p>;
  }

  const approval = detail.data.approval;
  const episode = detail.data.episode;
  const rejectDisabled = note.trim() === "";

  return (
    <section className="flex flex-col gap-16 rounded-card border border-primary-border bg-surface p-24 shadow-low">
      <div className="flex flex-wrap items-center justify-between gap-8">
        <div className="flex flex-wrap items-center gap-8">
          <StatusPill tone="notice" dot>
            gated — awaiting human decision
          </StatusPill>
          <span className="tnum text-xl font-semibold text-text-normal">
            {formatInr(approval.amount_paise)}
          </span>
          <span className="break-all font-mono text-xs text-text-muted" title={approval.id}>
            {approval.id}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close approval detail"
          className="flex h-control-sm w-control-sm items-center justify-center rounded-button border border-border-normal text-text-subtle hover:border-border-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-16 w-16" aria-hidden />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-16">
        <div className="flex flex-col gap-8 rounded-card bg-canvas p-16">
          <p className="text-xs font-medium uppercase text-text-muted">Proposed action</p>
          <p className="text-sm text-text-normal">{approval.proposed_action}</p>
          <div className="flex flex-wrap items-center gap-8">
            {approval.tier !== null && (
              <StatusPill tone="information">{`tier ${approval.tier}`}</StatusPill>
            )}
            <span className="tnum text-xs text-text-muted">
              outreach send · {formatInr(approval.amount_paise)}
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-8 rounded-card bg-canvas p-16">
          <p className="text-xs font-medium uppercase text-text-muted">Current state</p>
          <div className="flex flex-wrap items-center gap-8">
            <EpisodeStatePill state={episode.state} />
            {episode.cohort !== null && (
              <span className="text-xs font-medium text-text-muted">{episode.cohort}</span>
            )}
          </div>
          <p className="tnum font-mono text-xs text-text-muted">
            subscription {episode.subscription_id} · attempts {approval.attempt_count} · halted{" "}
            {timeAgo(episode.halt_ts_utc)}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-4">
        <p className="text-sm text-text-subtle">
          Gate reason: <span className="font-mono">{approval.reason}</span>
          {REASON_NOTES[approval.reason] !== undefined && (
            <span className="text-text-muted"> — {REASON_NOTES[approval.reason]}</span>
          )}
        </p>
        <ThresholdLine approval={approval} />
      </div>

      <div className="flex flex-col gap-8">
        <label htmlFor="approval-note" className="text-xs font-medium uppercase text-text-muted">
          Reason {rejectDisabled ? "(required to reject)" : "(recorded in the ledger)"}
        </label>
        <textarea
          id="approval-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          placeholder="Why this judgment? Recorded with the decision."
          className="rounded-button border border-border-normal bg-surface px-12 py-8 text-sm text-text-normal outline-none placeholder:text-text-disabled focus:border-primary-border focus:ring-2 focus:ring-primary-border"
        />
      </div>

      {error !== null && <ErrorNote message={error} />}

      <div className="flex gap-8">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide("approve")}
          className="h-control-md rounded-button bg-primary px-20 text-sm font-medium text-surface hover:bg-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === "approve" ? "Recording…" : "Approve"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void decide("reject")}
          title={note.trim() === "" ? "a reason is required to reject" : undefined}
          className="h-control-md rounded-button border border-negative-solid bg-surface px-20 text-sm font-medium text-negative-text hover:bg-negative-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === "reject" ? "Recording…" : "Reject"}
        </button>
        <span className="ml-auto hidden items-center gap-8 text-xs text-text-muted sm:flex">
          <kbd className="rounded-button border border-border-subtle bg-canvas px-4 py-1 font-mono text-[10px] text-text-muted">A</kbd>
          approve
          <kbd className="rounded-button border border-border-subtle bg-canvas px-4 py-1 font-mono text-[10px] text-text-muted">R</kbd>
          reject
        </span>
      </div>
    </section>
  );
}

function ThresholdLine({ approval }: { approval: ApprovalSummary }) {
  const { amount_paise, threshold_paise, exceeds_threshold, over_by_paise } = approval;
  return (
    <p className="tnum font-mono text-xs text-text-muted">
      threshold math: {formatInr(amount_paise)} (amount) {exceeds_threshold ? ">" : "≤"}{" "}
      {formatInr(threshold_paise)} (gate) —{" "}
      {exceeds_threshold
        ? `over by ${formatInr(over_by_paise)}; the amount itself crosses the human-gate threshold`
        : "the amount alone would not gate; the recorded tier is what requires a human"}
    </p>
  );
}

export function ApprovalsPage() {
  const pending = useApi<ApprovalsPendingResponse>("/api/approvals/pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const showSkeleton = useDelayedFlag(
    pending.data === null && pending.error === null,
  );

  if (pending.error !== null) {
    return (
      <ErrorState
        title="Couldn't load the approvals queue"
        message="The pending approvals failed to load. The API may be restarting — retry."
        onRetry={pending.refetch}
      />
    );
  }
  if (pending.data === null) {
    return showSkeleton ? (
      <div className="grid gap-16 lg:grid-cols-2">
        {Array.from({ length: 2 }, (_, i) => (
          <CardSkeleton key={i} label="Loading approval card" className="p-24" />
        ))}
      </div>
    ) : null;
  }
  const approvals = pending.data.approvals;

  if (approvals.length === 0) {
    return (
      <EmptyState
        icon={
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="5" y="4" width="14" height="17" rx="2" />
            <path d="M9 4.5V3h6v1.5" />
            <path d="M9 11l2 2 4-4" />
          </svg>
        }
        title="Nothing awaiting judgment"
        explanation="Episodes land here only when policy gates their outreach — a tier-3 escalation or an amount above the human-gate threshold. Nothing is gated right now."
      />
    );
  }

  return (
    <div className="flex flex-col gap-24">
      {confirmation !== null && (
        <div className="flex items-center gap-8 rounded-card bg-positive-bg p-12 text-sm font-medium text-positive-text">
          <CheckCircle2 className="h-16 w-16 shrink-0" aria-hidden />
          {confirmation}
        </div>
      )}
      <div className="grid gap-16 lg:grid-cols-2">
        {approvals.map((approval) => (
          <button
            key={approval.id}
            type="button"
            onClick={() => {
              setSelectedId(approval.id);
              setConfirmation(null);
            }}
            className={cn(
              "flex flex-col gap-8 rounded-card border p-16 text-left shadow-low",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selectedId === approval.id
                ? "border-primary-border bg-primary-tint"
                : "border-border-subtle bg-surface hover:border-border-hover",
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-8">
              <span className="tnum text-xl font-semibold text-text-normal">
                {formatInr(approval.amount_paise)}
              </span>
              <StatusPill tone="neutral">{approval.episode_state}</StatusPill>
            </div>
            <p className="break-all font-mono text-xs text-text-muted" title={approval.episode_id}>
              {approval.episode_id}
            </p>
            <p className="text-sm text-text-subtle">
              Gate reason: <span className="font-mono">{approval.reason}</span>
            </p>
            <div className="flex items-center justify-between">
              <span className="text-xs text-text-muted" title={approval.created_ts_utc}>
                queued {timeAgo(approval.created_ts_utc)}
              </span>
              <span className="text-sm font-medium text-primary hover:text-primary-hover">
                Review →
              </span>
            </div>
          </button>
        ))}
      </div>
      {selectedId !== null && (
        <ApprovalDetail
          approvalId={selectedId}
          onClose={() => setSelectedId(null)}
          onDecided={(message) => {
            setConfirmation(message);
            setSelectedId(null);
            pending.refetch();
          }}
        />
      )}
    </div>
  );
}
