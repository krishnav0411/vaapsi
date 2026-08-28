/**
 * D7.4 Metrics M1–M5, rendered verbatim from /api/metrics: name, value
 * (with its unit — % / ₹ / hours), n and the metric's own note — the
 * denominator travels with every value because honesty is the design.
 * Undefined values (0/0 rates, empty medians, CONTROL with no halts)
 * render "—" + the note, never a fake zero. Every card carries a
 * provenance tooltip stating the metric's real query semantics; the
 * percentage values count up. Below the cards: the same provenance
 * strip Overview carries and the EXPERIMENT.md pointer, so the page
 * states where its definitions were frozen.
 */

import { CountUp } from "@/components/CountUp";
import { ErrorState } from "@/components/ErrorState";
import { Provenance } from "@/components/Provenance";
import { ProvenanceStrip } from "@/components/ProvenanceStrip";
import { CardSkeleton } from "@/components/Skeleton";
import { useDelayedFlag } from "@/hooks/useDelayedFlag";
import { useApi, type Metric, type OverviewResponse } from "@/lib/api";
import { formatInr, formatPct } from "@/lib/format";

const PCT_METRICS = new Set([
  "M1_recovery_rate_TREATMENT",
  "M1_recovery_rate_CONTROL",
  "M4_outreach_efficiency",
]);

function metricValue(metric: Metric) {
  if (metric.value === null) return "—";
  if (metric.name === "M2_recovered_paise") return formatInr(metric.value);
  if (metric.name === "M3_time_to_recover_hours_median") return `${metric.value.toFixed(1)} h`;
  if (PCT_METRICS.has(metric.name)) {
    return (
      <CountUp value={metric.value * 100} format={(n) => formatPct(n / 100)} />
    );
  }
  return `${metric.value}`;
}

/** Truthful derivation per metric — mirrors app/dashboard/metrics.py. */
function metricProvenance(metric: Metric): string {
  switch (metric.name) {
    case "M1_recovery_rate_TREATMENT":
      return `recoveries within 7d of halt / total TREATMENT halts, n=${metric.n}`;
    case "M1_recovery_rate_CONTROL":
      return `recoveries within 7d of halt / total CONTROL halts, n=${metric.n}`;
    case "M2_recovered_paise":
      return `sum of recovered_paise in the ledger over rows with recovered_paise > 0, n=${metric.n} rows`;
    case "M3_time_to_recover_hours_median":
      return `median hours from the halt's EPISODE_CREATED ledger row to the subscription's first recovered row, across n=${metric.n} subscriptions`;
    case "M4_outreach_efficiency":
      return `distinct subscriptions recovered / EPISODE_SENT ledger rows, n=${metric.n} outreach sends`;
    case "M5_false_outreach":
      return `EPISODE_SENT rows fired at/after a stop void, checked against n=${metric.n} stop voids in the ledger`;
    default:
      return `computed from the audit ledger, n=${metric.n}`;
  }
}

export function MetricsPage() {
  const metrics = useApi<Metric[]>("/api/metrics");
  const overview = useApi<OverviewResponse>("/api/overview");
  const showSkeleton = useDelayedFlag(
    metrics.data === null && metrics.error === null,
  );

  if (metrics.error !== null) {
    return (
      <ErrorState
        title="Couldn't load the metrics"
        message="The metric definitions failed to load. The API may be restarting — retry."
        onRetry={metrics.refetch}
      />
    );
  }
  if (metrics.data === null) {
    return showSkeleton ? (
      <div className="grid grid-cols-3 gap-16">
        {Array.from({ length: 6 }, (_, i) => (
          <CardSkeleton key={i} label="Loading metric card" />
        ))}
      </div>
    ) : null;
  }

  return (
    <div className="flex flex-col gap-24">
      <div className="grid grid-cols-3 gap-16">
        {metrics.data.map((metric) => (
          <div
            key={metric.name}
            className="flex flex-col gap-4 rounded-card border border-border-subtle bg-surface p-24 shadow-low"
          >
            <div className="flex items-center justify-between gap-8">
              <p className="font-mono text-xs uppercase text-text-muted">{metric.name}</p>
              <Provenance>{metricProvenance(metric)}</Provenance>
            </div>
            <p className="tnum text-3xl font-semibold text-text-normal">{metricValue(metric)}</p>
            <p className="tnum text-xs text-text-muted">n={metric.n}</p>
            <p className="text-xs text-text-muted">{metric.note}</p>
          </div>
        ))}
      </div>

      <ProvenanceStrip cohorts={overview.data?.cohorts} />

      <p className="text-xs text-text-muted">
        Pre-registered in EXPERIMENT.md before any halt data existed.
      </p>
    </div>
  );
}
