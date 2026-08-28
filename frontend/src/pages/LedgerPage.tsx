/**
 * D8 Ledger explorer — the audit chain as a block explorer. The table
 * shows the chain-ordered rows exactly as the server truncates them
 * (prev 12 / hash 16) with per-row copy buttons and server pagination
 * (50 rows per page); clicking a row expands the FULL detail (64-char
 * hashes, the policy_eval JSON, and the canonical JSON the verifier
 * hashes — the exact replay material). The header carries the live
 * chain-verdict chip, a Verify button that plays a staggered per-row
 * check animation off the real verifier result, and the tamper demo:
 * the one place the word tamper is allowed — it runs on a throwaway
 * COPY of the store, never the live one.
 */

import { Fragment, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Check, ChevronDown, ShieldAlert, X } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Provenance } from "@/components/Provenance";
import { StatusPill } from "@/components/StatusPill";
import { TableSkeleton } from "@/components/Skeleton";
import { useDelayedFlag } from "@/hooks/useDelayedFlag";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  getLedgerRow,
  runTamperDemo,
  useApi,
  verifyLedger,
  type LedgerListResponse,
  type LedgerRowDetail,
  type LedgerVerifyResponse,
  type TamperDemoResponse,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const VERIFY_STEP_MS = 80;

/** The staggered per-row verify animation state (null = idle). */
interface CheckAnimation {
  /** How many of `seqs` are currently marked (front of the list). */
  checked: number;
  /** The seqs being walked, in chain order (the current page). */
  seqs: number[];
  /** The broken seq from the verifier, when the chain is broken. */
  brokenSeq: number | null;
}

function CopyAllButton({ detail }: { detail: LedgerRowDetail }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(JSON.stringify(detail, null, 2));
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard unavailable — every value is visible text anyway.
        }
      }}
      className="rounded-button border border-border-normal px-12 py-2 text-xs font-medium text-text-subtle hover:border-border-hover hover:text-text-normal"
    >
      {copied ? "Copied" : "Copy all"}
    </button>
  );
}

/** 64-char hash rendered truncated with an expand toggle. */
function HashValue({ hash }: { hash: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <span className="flex min-w-0 items-center gap-8">
      <span
        className="min-w-0 break-all font-mono text-xs text-text-normal"
        title={expanded ? undefined : hash}
      >
        {expanded ? hash : `${hash.slice(0, 24)}…`}
      </span>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="shrink-0 text-xs font-medium text-primary hover:text-primary-hover"
      >
        {expanded ? "shrink" : "full"}
      </button>
    </span>
  );
}

function DetailFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className="text-[10px] font-medium uppercase text-text-muted">{label}</span>
      <span className="break-all font-mono text-xs text-text-normal" title={value}>
        {value}
      </span>
    </div>
  );
}

function LedgerRowDetailPanel({ seq }: { seq: number }) {
  const [detail, setDetail] = useState<LedgerRowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    getLedgerRow(seq)
      .then((row) => {
        if (alive) setDetail(row);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "request failed");
      });
    return () => {
      alive = false;
    };
  }, [seq]);

  if (error !== null) {
    return <p role="alert" className="bg-negative-bg p-12 text-sm font-medium text-negative-text">{error}</p>;
  }
  if (detail === null) {
    return <p className="p-12 text-sm text-text-muted">Loading row {seq}…</p>;
  }

  const prettyPolicy =
    detail.policy_eval === null
      ? "null"
      : JSON.stringify(detail.policy_eval, null, 2);

  return (
    <div className="flex flex-col gap-16 border-b border-border-subtle bg-row-hover p-16">
      <div className="flex flex-wrap items-center justify-between gap-8">
        <p className="text-xs font-medium uppercase text-text-muted">
          Row {seq} · full detail
        </p>
        <CopyAllButton detail={detail} />
      </div>

      <div className="grid grid-cols-3 gap-12">
        <DetailHashRow label="prev_hash (full 64)" hash={detail.prev_hash} />
        <DetailHashRow label="row_hash (full 64)" hash={detail.row_hash} />
        <div className="grid grid-cols-2 gap-8">
          <DetailFact label="prev_seq" value={detail.prev_seq === null ? "—" : `${detail.prev_seq}`} />
          <DetailFact label="score" value={detail.score === null ? "—" : `${detail.score}`} />
          <DetailFact label="human_gate" value={detail.human_gate ? "true" : "false"} />
          <DetailFact label="recovered" value={`${detail.recovered_paise} p`} />
          <DetailFact label="mode" value={detail.mode} />
          <DetailFact label="llm_model" value={detail.llm_model ?? "—"} />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        <p className="text-xs font-medium uppercase text-text-muted">policy_eval</p>
        <pre className="max-h-192 overflow-auto rounded-button border border-border-subtle bg-surface p-12 font-mono text-xs text-text-subtle">
          {prettyPolicy}
        </pre>
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        <p className="text-xs font-medium uppercase text-text-muted">
          canonical_json — exactly the material row_hash commits to
        </p>
        <pre className="overflow-x-auto whitespace-pre rounded-button border border-border-subtle bg-surface p-12 font-mono text-xs text-text-subtle">
          {detail.canonical_json}
        </pre>
      </div>
    </div>
  );
}

function DetailHashRow({ label, hash }: { label: string; hash: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <span className="text-xs font-medium uppercase text-text-muted">{label}</span>
      <span className="flex items-start gap-8">
        <span className="min-w-0 break-all font-mono text-xs text-text-normal" title={hash}>
          {hash}
        </span>
        <CopyButtonSmall value={hash} />
      </span>
    </div>
  );
}

function CopyButtonSmall({ value }: { value: string }) {
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
          // Clipboard unavailable — value is visible text anyway.
        }
      }}
      className="shrink-0 rounded-button border border-border-normal px-8 py-1 text-[10px] font-medium text-text-subtle hover:border-border-hover hover:text-text-normal"
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/** The "Prove it" card: the tamper demo, run on a copy, never the live store. */
function TamperDemoCard() {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TamperDemoResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function fire() {
    setRunning(true);
    setError(null);
    try {
      setResult(await runTamperDemo());
    } catch (err) {
      setError(err instanceof Error ? err.message : "tamper demo failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="flex flex-col gap-16 rounded-card border-2 border-negative-solid bg-surface p-24 shadow-high">
      <div className="flex flex-wrap items-center justify-between gap-12">
        <div className="flex flex-col gap-4">
          <h2 className="font-display text-lg font-semibold text-text-normal">
            Prove it: tamper demo
          </h2>
          <p className="max-w-560 text-sm text-text-subtle">
            This copies the database into a throwaway sandbox, edits exactly one
            value on the copy, and runs the real chain verifier — which must name
            the broken row. The live store is opened read-only and never written.
          </p>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              type="button"
              disabled={running}
              className="inline-flex h-control-md shrink-0 items-center gap-8 rounded-button bg-negative-solid px-20 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldAlert className="h-16 w-16" aria-hidden />
              {running ? "Running on the copy…" : "Run tamper demo"}
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Run the tamper demo?</AlertDialogTitle>
              <AlertDialogDescription>
                Vaapsi will copy the store, flip one recorded value on the copy, and
                let the verifier name the broken row. The live store is never
                written — the copy is deleted when the demo ends.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={(event) => { event.preventDefault(); void fire(); }}>
                Run on a copy
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {error !== null && (
        <p role="alert" className="rounded-card bg-negative-bg p-12 text-sm font-medium text-negative-text">
          {error}
        </p>
      )}

      {result !== null && (
        <TamperVerdict result={result} />
      )}
    </section>
  );
}

function TamperVerdict({ result }: { result: TamperDemoResponse }) {
  if (result.verdict === "empty_ledger") {
    return (
      <div className="rounded-card bg-notice-bg p-16">
        <StatusPill tone="notice" dot>
          empty ledger — nothing to tamper
        </StatusPill>
        <p className="mt-8 text-sm text-notice-text">{result.verify_detail}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-16">
      <div className="flex flex-col gap-12 rounded-card bg-negative-bg p-16">
        <div className="flex flex-wrap items-center gap-12">
          <StatusPill tone="negative" dot>
            tamper_detected
          </StatusPill>
          <p className="font-display text-lg font-semibold text-negative-text">
            The verifier named the broken row.
          </p>
        </div>
        <p className="font-mono text-sm text-negative-text">
          row seq {result.broken_seq} · field{" "}
          <span className="font-semibold">{result.field}</span>
        </p>
        <p className="tnum font-mono text-sm text-negative-text">
          expected {result.expected_value} → found {result.found_value} (a one-paise lie)
        </p>
        <div className="grid gap-8 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-4 rounded-button border border-border-subtle bg-surface p-12">
            <span className="text-xs font-medium uppercase text-text-muted">
              stored_hash (the honest commitment)
            </span>
            {result.stored_hash !== null && <HashValue hash={result.stored_hash} />}
          </div>
          <div className="flex min-w-0 flex-col gap-4 rounded-button border border-border-subtle bg-surface p-12">
            <span className="text-xs font-medium uppercase text-text-muted">
              recomputed from tampered contents
            </span>
            {result.recomputed_hash !== null && <HashValue hash={result.recomputed_hash} />}
          </div>
        </div>
        <p className="break-all font-mono text-xs text-negative-text" title={result.verify_detail}>
          verifier: {result.verify_detail}
        </p>
      </div>
      {result.original_store_chain_valid && (
        <div>
          <StatusPill tone="positive" dot>
            original store untouched · chain valid · {result.original_rows} rows
          </StatusPill>
        </div>
      )}
    </div>
  );
}

export function LedgerPage() {
  const [page, setPage] = useState(0);
  const list = useApi<LedgerListResponse>(
    `/api/ledger?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`,
  );
  const verify = useApi<LedgerVerifyResponse>("/api/ledger/verify");
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [check, setCheck] = useState<CheckAnimation | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  // Deep link (?seq=N from the command palette): jump to the owning page,
  // expand the row, then clean the URL.
  useEffect(() => {
    const seqParam = searchParams.get("seq");
    if (seqParam === null || list.data === null) return;
    const seq = Number(seqParam);
    setSearchParams({}, { replace: true });
    if (!Number.isInteger(seq) || seq < 1 || seq > list.data.total) return;
    setPage(Math.floor((seq - 1) / PAGE_SIZE));
    setExpandedSeq(seq);
  }, [searchParams, list.data, setSearchParams]);

  useEffect(() => {
    if (check === null) return;
    const stopHere = check.brokenSeq !== null && check.seqs[check.checked - 1] === check.brokenSeq;
    if (check.checked < check.seqs.length && !stopHere) {
      const timer = setTimeout(
        () => setCheck((current) => (current === null ? null : { ...current, checked: current.checked + 1 })),
        VERIFY_STEP_MS,
      );
      return () => clearTimeout(timer);
    }
    const settle = setTimeout(() => setCheck(null), stopHere ? 2600 : 1400);
    return () => clearTimeout(settle);
  }, [check]);

  async function runVerify() {
    setVerifyError(null);
    try {
      const result = await verifyLedger();
      verify.refetch();
      const seqs = (list.data?.rows ?? []).map((row) => row.seq);
      setCheck({ checked: 0, seqs, brokenSeq: result.valid ? null : result.broken_seq });
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "verify request failed");
    }
  }

  const rows = list.data?.rows ?? [];
  const total = list.data?.total ?? 0;
  const pageEnd = page * PAGE_SIZE + rows.length;
  const showSkeleton = useDelayedFlag(list.data === null && list.error === null);

  return (
    <div className="flex flex-col gap-24">
      <section className="flex flex-wrap items-center justify-between gap-16 rounded-card border border-border-subtle bg-surface p-24 shadow-low">
        <div className="flex flex-col gap-8">
          <h2 className="font-display text-lg font-semibold text-text-normal">
            Audit chain
          </h2>
          {verify.error !== null ? (
            <p role="alert" className="text-sm font-medium text-negative-text">
              {verify.error}
            </p>
          ) : verify.data === null ? (
            <span className="text-sm text-text-muted">checking chain…</span>
          ) : verify.data.valid ? (
            <span className="flex items-center gap-4">
              <StatusPill tone="positive" dot>
                {`chain valid · ${verify.data.rows} rows`}
              </StatusPill>
              <Provenance>
                sha256 chain over all rows; verify recomputes every link and names the
                first broken one
              </Provenance>
            </span>
          ) : (
            <StatusPill tone="negative" dot>
              {`chain broken @ seq ${verify.data.broken_seq ?? "?"}`}
            </StatusPill>
          )}
        </div>
        <div className="flex items-center gap-8">
          {verifyError !== null && (
            <p role="alert" className="text-sm font-medium text-negative-text">
              {verifyError}
            </p>
          )}
          <button
            type="button"
            onClick={() => void runVerify()}
            disabled={check !== null || list.data === null}
            className="h-control-md rounded-button border border-primary px-20 text-sm font-medium text-primary hover:bg-primary-tint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          >
            {check !== null ? "Verifying…" : "Verify chain"}
          </button>
        </div>
      </section>

      <TamperDemoCard />

      {list.error !== null ? (
        <ErrorState
          title="Couldn't load the ledger"
          message="The ledger rows failed to load. The API may be restarting — retry."
          onRetry={list.refetch}
        />
      ) : list.data === null ? (
        showSkeleton ? (
          <TableSkeleton rows={8} cols={8} label="Loading ledger" />
        ) : null
      ) : rows.length === 0 ? (
        <EmptyState
          icon={
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
          }
          title="No ledger rows yet"
          explanation="The audit chain starts when the first halt is recorded — every agent action appends one verified row."
        />
      ) : (
        <>
          <div className="overflow-hidden rounded-card border border-border-subtle bg-surface shadow-low">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-border-subtle">
                  <th className="w-24 px-12 py-8" aria-label="verification mark" />
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">Seq</th>
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">Trigger</th>
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">Outcome</th>
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">Subscription</th>
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">When</th>
                  <th className="px-12 py-8 text-xs font-medium uppercase text-text-muted">prev → hash</th>
                  <th className="px-12 py-8" aria-label="row actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const expanded = expandedSeq === row.seq;
                  const markIndex = check?.seqs.indexOf(row.seq) ?? -1;
                  const marked = check !== null && markIndex !== -1 && markIndex < check.checked;
                  const broken = marked && check?.brokenSeq === row.seq;
                  return (
                    <Fragment key={row.seq}>
                      <tr
                        onClick={() => setExpandedSeq(expanded ? null : row.seq)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setExpandedSeq(expanded ? null : row.seq);
                          }
                        }}
                        tabIndex={0}
                        aria-expanded={expanded}
                        className={cn(
                          "cursor-pointer border-b border-border-subtle hover:bg-row-hover",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                          expanded && "bg-row-hover",
                          broken && "bg-negative-bg",
                        )}
                      >
                        <td className="px-12 py-8">
                          {marked &&
                            (check?.brokenSeq === row.seq ? (
                              <X className="h-16 w-16 text-negative-solid" aria-label="broken row" />
                            ) : (
                              <Check className="h-16 w-16 text-positive-solid" aria-label="verified" />
                            ))}
                        </td>
                        <td className="tnum px-12 py-8 font-mono text-sm text-text-normal">{row.seq}</td>
                        <td className="px-12 py-8">
                          <span className="inline-flex items-center gap-8">
                            <ChevronDown
                              aria-hidden
                              className={cn(
                                "h-16 w-16 shrink-0 text-text-muted transition-transform",
                                expanded && "rotate-180",
                              )}
                            />
                            <span className="font-mono text-xs text-text-subtle">{row.trigger_event}</span>
                          </span>
                        </td>
                        <td className="px-12 py-8 font-mono text-xs text-text-subtle">{row.outcome}</td>
                        <td className="px-12 py-8 font-mono text-xs text-text-muted" title={row.subscription_id}>
                          {row.subscription_id}
                        </td>
                        <td className="tnum px-12 py-8 text-xs text-text-muted" title={row.ts_utc}>
                          {timeAgo(row.ts_utc)}
                        </td>
                        <td className="px-12 py-8 font-mono text-xs text-text-subtle">
                          <span title={`${row.prev_hash} → ${row.hash}`}>
                            {row.prev_hash} → {row.hash}
                          </span>
                        </td>
                        <td className="px-12 py-8" onClick={(event) => event.stopPropagation()}>
                          <CopyButtonSmall value={`${row.prev_hash} → ${row.hash}`} />
                        </td>
                      </tr>
                      {expanded && (
                        <tr>
                          <td colSpan={8} className="p-0">
                            <LedgerRowDetailPanel seq={row.seq} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between">
            <p className="tnum text-sm text-text-muted">
              seq {rows.length === 0 ? 0 : page * PAGE_SIZE + 1}–{pageEnd} of {total}
            </p>
            <div className="flex gap-8">
              <button
                type="button"
                onClick={() => setPage((current) => Math.max(0, current - 1))}
                disabled={page === 0}
                className="h-control-sm rounded-button border border-border-normal px-16 text-sm font-medium text-text-subtle hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                ← Prev
              </button>
              <button
                type="button"
                onClick={() => setPage((current) => current + 1)}
                disabled={list.data === null || pageEnd >= total}
                className="h-control-sm rounded-button border border-border-normal px-16 text-sm font-medium text-text-subtle hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
