/**
 * D8 Drills console — one card per drill from /api/drills. "Run drill"
 * fires POST /drills/{id}/run synchronously (bounded <30s, always against
 * an isolated throwaway store) and renders the honest result: green check
 * + summary when passed, red + the evidence JSON verbatim when failed.
 * Last results live in component state only; the catalog's `last_run`
 * (this server process' memory) renders as a secondary hint. The
 * top-of-page note states the isolation contract in one line.
 */

import { useState } from "react";
import { Check, Loader2, Play, X } from "lucide-react";

import { StatusPill } from "@/components/StatusPill";
import { runDrill, useApi, type DrillRunResult, type DrillsResponse } from "@/lib/api";
import { timeAgo } from "@/lib/format";

function ResultPanel({ result }: { result: DrillRunResult }) {
  if (result.passed) {
    return (
      <div className="flex flex-col gap-8 rounded-card bg-positive-bg p-16">
        <div className="flex items-center gap-8">
          <Check className="h-16 w-16 shrink-0 text-positive-solid" aria-hidden />
          <StatusPill tone="positive">passed</StatusPill>
          <span className="tnum text-xs text-positive-text">
            {result.duration_ms} ms
          </span>
        </div>
        <p className="text-sm text-positive-text">{result.summary}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-8 rounded-card bg-negative-bg p-16">
      <div className="flex items-center gap-8">
        <X className="h-16 w-16 shrink-0 text-negative-solid" aria-hidden />
        <StatusPill tone="negative">failed</StatusPill>
        <span className="tnum text-xs text-negative-text">{result.duration_ms} ms</span>
      </div>
      <p className="text-sm font-medium text-negative-text">{result.summary}</p>
      <pre className="max-h-240 overflow-auto rounded-button bg-surface p-12 font-mono text-xs text-negative-text">
        {JSON.stringify(result.evidence, null, 2)}
      </pre>
    </div>
  );
}

export function DrillsPage() {
  const drills = useApi<DrillsResponse>("/api/drills");
  const [runningId, setRunningId] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, DrillRunResult>>({});
  const [error, setError] = useState<string | null>(null);

  async function run(drillId: string) {
    setRunningId(drillId);
    setError(null);
    try {
      const result = await runDrill(drillId);
      setResults((current) => ({ ...current, [drillId]: result }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "drill request failed");
    } finally {
      setRunningId(null);
    }
  }

  if (drills.error !== null) {
    return (
      <div role="alert" className="rounded-card bg-negative-bg p-16 text-sm font-medium text-negative-text">
        {drills.error}
      </div>
    );
  }
  if (drills.data === null) {
    return <p className="text-sm text-text-muted">Loading drills…</p>;
  }

  return (
    <div className="flex flex-col gap-24">
      <p className="text-sm text-text-muted">
        Drills run against isolated throwaway stores — the live ledger is never touched.
      </p>

      <div className="flex flex-col gap-16">
        {drills.data.drills.map((drill) => {
          const result = results[drill.drill_id];
          const isRunning = runningId === drill.drill_id;
          return (
            <section
              key={drill.drill_id}
              className="flex flex-col gap-12 rounded-card border border-border-subtle bg-surface p-24 shadow-low"
            >
              <div className="flex flex-wrap items-center justify-between gap-8">
                <h2 className="font-display text-lg font-semibold text-text-normal">
                  {drill.title}
                </h2>
                {isRunning ? (
                  <span className="inline-flex h-control-sm items-center gap-8 rounded-button bg-info-bg px-12 text-sm font-medium text-info-text">
                    <Loader2 className="h-16 w-16 animate-spin" aria-hidden />
                    running… (up to 30s)
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => void run(drill.drill_id)}
                    disabled={runningId !== null}
                    className="inline-flex h-control-sm items-center gap-8 rounded-button bg-primary px-16 text-sm font-medium text-surface hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Play className="h-16 w-16" aria-hidden />
                    Run drill
                  </button>
                )}
              </div>

              <p className="max-w-720 text-sm text-text-subtle">{drill.description}</p>

              {drill.last_run !== null && result === undefined && (
                <p className="text-xs text-text-muted" title={drill.last_run.ran_ts_utc}>
                  last run this process: {drill.last_run.passed ? "passed" : "failed"} ·{" "}
                  {drill.last_run.summary} · {timeAgo(drill.last_run.ran_ts_utc)}
                </p>
              )}

              {result !== undefined && <ResultPanel result={result} />}
            </section>
          );
        })}
      </div>

      {error !== null && (
        <p role="alert" className="rounded-card bg-negative-bg p-16 text-sm font-medium text-negative-text">
          {error}
        </p>
      )}
    </div>
  );
}
