/**
 * The vertical agent timeline (D7.4 centerpiece): every ledger row of
 * the episode's cycle, in seq order, as one node — a Blade-token dot,
 * the trigger event, a relative timestamp with the ISO stamp as its
 * tooltip, and the row's real evidence (mode, policy summary, score,
 * variant, rzp payload, recovered amount) as nested muted mono lines.
 * Nodes connect on a 1px border-subtle rail. Nothing is inferred: a
 * field the API does not carry (e.g. llm_model, which _episode_ledger
 * does not select) simply does not render.
 *
 * Dot tones follow the Blade mapping: CREATED/
 * DIAGNOSED/SCORED → information blue, SENT → information solid,
 * VERIFIED → positive, DLQ/DISPATCH_ERROR/void rows → negative,
 * BLOCKED and gate rows → notice, everything unlabeled → neutral.
 */

import type { LedgerRow } from "@/lib/api";
import { formatInr, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

function dotClass(row: LedgerRow): string {
  const outcome = row.outcome;
  if (outcome === "EPISODE_VERIFIED") return "bg-positive-solid";
  if (outcome === "EPISODE_SENT") return "bg-info-solid";
  if (
    outcome === "EPISODE_CREATED" ||
    outcome === "EPISODE_DIAGNOSED" ||
    outcome === "EPISODE_SCORED"
  ) {
    return "bg-info-text";
  }
  if (
    outcome === "EPISODE_VOIDED" ||
    outcome.includes("DLQ") ||
    outcome.includes("DISPATCH_ERROR")
  ) {
    return "bg-negative-solid";
  }
  if (outcome === "EPISODE_GATED" || outcome.includes("BLOCKED")) {
    return "bg-notice-solid";
  }
  return "bg-border-normal";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** policy_eval as nested mono lines — the same digest routes.py renders. */
function policyLines(policyEval: unknown): string[] {
  if (!isRecord(policyEval)) return [];
  const lines: string[] = [];
  if (typeof policyEval.decision === "string") lines.push(`decision ${policyEval.decision}`);
  if (typeof policyEval.ok === "boolean") lines.push(`ok ${policyEval.ok}`);
  if (typeof policyEval.action === "string") lines.push(`action ${policyEval.action}`);
  if (typeof policyEval.from_state === "string" || typeof policyEval.to_state === "string") {
    lines.push(`${policyEval.from_state ?? "·"} → ${policyEval.to_state ?? "·"}`);
  }
  if (typeof policyEval.reason === "string") lines.push(`reason ${policyEval.reason}`);
  if (typeof policyEval.tier === "number") lines.push(`tier ${policyEval.tier}`);
  if (typeof policyEval.rationale === "string") lines.push(`rationale ${policyEval.rationale}`);
  if (isRecord(policyEval.choice) && typeof policyEval.choice.message_variant === "string") {
    lines.push(`variant ${policyEval.choice.message_variant}`);
  }
  if (typeof policyEval.approval_id === "string") lines.push(`approval ${policyEval.approval_id}`);
  if (typeof policyEval.gate_reason === "string") lines.push(`gate_reason ${policyEval.gate_reason}`);
  if (typeof policyEval.dispatch_error === "string") {
    lines.push(`dispatch_error ${policyEval.dispatch_error}`);
  }
  if (typeof policyEval.dlq_id === "string") lines.push(`dlq_id ${policyEval.dlq_id}`);
  if (isRecord(policyEval.details)) {
    lines.push(`details ${JSON.stringify(policyEval.details)}`);
  }
  return lines;
}

/** rzp_call (canonical JSON text) → one truncated evidence line. */
function rzpLine(rzpCall: string | null): string | null {
  if (rzpCall === null) return null;
  try {
    const parsed: unknown = JSON.parse(rzpCall);
    const text = isRecord(parsed) ? JSON.stringify(parsed) : rzpCall;
    return text.length > 140 ? `${text.slice(0, 140)}…` : text;
  } catch {
    return rzpCall.length > 140 ? `${rzpCall.slice(0, 140)}…` : rzpCall;
  }
}

function modeClass(mode: string): string {
  if (mode === "KILLED") return "text-negative-text";
  if (mode === "DEGRADED") return "text-notice-text";
  return "text-text-muted";
}

export function AgentTimeline({ rows }: { rows: LedgerRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="rounded-card border border-border-subtle bg-surface p-24 text-sm text-text-muted">
        No ledger rows in this episode's cycle yet — the chain starts with the halt.
      </p>
    );
  }

  return (
    <ol className="flex flex-col">
      {rows.map((row, index) => {
        const rzp = rzpLine(row.rzp_call);
        return (
          <li key={row.seq} className="relative flex gap-16 pb-24 last:pb-0">
            <div className="flex w-12 shrink-0 flex-col items-center">
              <span
                aria-hidden
                className={cn("mt-4 h-12 w-12 shrink-0 rounded-pill", dotClass(row))}
              />
              {index < rows.length - 1 && (
                <span aria-hidden className="w-px grow bg-border-subtle" />
              )}
            </div>
            <div className="flex min-w-0 grow flex-col gap-4">
              <div className="flex flex-wrap items-baseline gap-8">
                <p className="font-mono text-sm font-medium text-text-normal">
                  {row.trigger_event}
                </p>
                <span className="font-mono text-xs text-text-disabled">{row.outcome}</span>
                <span className="ml-auto text-sm text-text-muted" title={row.ts_utc}>
                  {timeAgo(row.ts_utc)}
                </span>
              </div>
              <div className="flex flex-col gap-2">
                <p className={cn("font-mono text-xs", modeClass(row.mode))}>mode {row.mode}</p>
                {policyLines(row.policy_eval).map((line) => (
                  <p key={line} className="break-all font-mono text-xs text-text-muted">
                    {line}
                  </p>
                ))}
                {row.score !== null && (
                  <p className="tnum font-mono text-xs text-text-muted">score {row.score}</p>
                )}
                {rzp !== null && (
                  <p className="break-all font-mono text-xs text-text-muted" title={row.rzp_call ?? undefined}>
                    rzp {rzp}
                  </p>
                )}
                {row.recovered_paise > 0 && (
                  <p className="tnum font-mono text-xs font-medium text-positive-text">
                    recovered {formatInr(row.recovered_paise)}
                  </p>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
