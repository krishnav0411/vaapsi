/**
 * D7.4 Metrics M1–M5, rendered verbatim from /api/metrics: name, value
 * (with its unit — % / ₹ / hours), n and the metric's own note — the
 * denominator travels with every value because honesty is the design.
 * Undefined values (0/0 rates, empty medians, CONTROL with no halts)
 * render "—" + the note, never a fake zero. Below the cards: the same
 * provenance strip Overview carries and the EXPERIMENT.md pointer, so
 * the page states where its definitions were frozen.
 */

import { ProvenanceStrip } from "@/components/ProvenanceStrip";
import { useApi, type Metric, type OverviewResponse } from "@/lib/api";
import { formatInr, formatPct } from "@/lib/format";

const PCT_METRICS = new Set([
  "M1_recovery_rate_TREATMENT",
  "M1_recovery_rate_CONTROL",
  "M4_outreach_efficiency",
]);

function metricValue(metric: Metric): string {
  if (metric.value === null) return "—";
  if (metric.name === "M2_recovered_paise") return formatInr(metric.value);
  if (metric.name === "M3_time_to_recover_hours_median") return `${metric.value.toFixed(1)} h`;
  if (PCT_METRICS.has(metric.name)) return formatPct(metric.value);
  return `${metric.value}`;
}

export function MetricsPage() {
  const metrics = useApi<Metric[]>("/api/metrics");
  const overview = useApi<OverviewResponse>("/api/overview");

  if (metrics.error !== null) {
    return (
      <div
        role="alert"
        className="rounded-card bg-negative-bg p-16 text-sm font-medium text-negative-text"
      >
        {metrics.error}
      </div>
    );
  }
  if (metrics.data === null) {
    return <p className="text-sm text-text-muted">Loading metrics…</p>;
  }

  return (
    <div className="flex flex-col gap-24">
      <div className="grid grid-cols-3 gap-16">
        {metrics.data.map((metric) => (
          <div
            key={metric.name}
            className="flex flex-col gap-4 rounded-card border border-border-subtle bg-surface p-24 shadow-low"
          >
            <p className="font-mono text-xs uppercase text-text-muted">{metric.name}</p>
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
