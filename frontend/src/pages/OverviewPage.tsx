/**
 * D7.3 Overview — the hero page. Opens on the role's first question
 * (money recovered), then the 5-card KPI band, the cohort provenance
 * strip, and the recent-activity table. All numbers come verbatim from
 * /api/overview (+ /api/metrics for M5, + /api/episodes for the table);
 * the CONTROL rate renders "—" + "no data" when n=0 — never a fake 0%.
 */

import { Link } from "react-router-dom";

import { EpisodeTable } from "@/components/EpisodeTable";
import { MetricCard } from "@/components/MetricCard";
import { ProvenanceStrip } from "@/components/ProvenanceStrip";
import { StatusPill } from "@/components/StatusPill";
import {
  useApi,
  type EpisodeRow,
  type Metric,
  type OverviewResponse,
} from "@/lib/api";
import { formatInr, formatPct } from "@/lib/format";

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

export function OverviewPage() {
  const overview = useApi<OverviewResponse>("/api/overview");
  const episodes = useApi<EpisodeRow[]>("/api/episodes");
  const metrics = useApi<Metric[]>("/api/metrics");

  const { data, error } = overview;
  if (error !== null) return <ErrorNote message={error} />;
  if (data === null) return <p className="text-sm text-text-muted">Loading overview…</p>;

  const { stats, cohorts } = data;
  const rateT = stats.recovery_rate_treatment;
  const rateC = stats.recovery_rate_control;
  const recoveredCount = rateT.value === null ? 0 : Math.round(rateT.value * rateT.n);
  const m5 = metrics.data?.find((metric) => metric.name === "M5_false_outreach") ?? null;

  return (
    <div className="flex flex-col gap-24">
      <div className="grid grid-cols-2 gap-24">
        <MetricCard
          size="hero"
          label="Recovered"
          value={formatInr(stats.recovered_paise)}
          sub={
            stats.recovered_paise_n === 0
              ? "n=0 — awaiting first payment"
              : `n=${stats.recovered_paise_n}`
          }
          note={stats.recovered_paise_note}
        />
        <MetricCard
          size="hero"
          label="Recovery rate · treatment"
          value={rateT.value === null ? "—" : formatPct(rateT.value)}
          sub={
            rateT.value === null
              ? "no data"
              : `${recoveredCount} of ${rateT.n} within 7d`
          }
          note={rateT.note}
        />
      </div>

      <div className="grid grid-cols-5 gap-12">
        <MetricCard
          label="Recovery rate · T"
          value={rateT.value === null ? "—" : formatPct(rateT.value)}
          sub={`${recoveredCount} of ${rateT.n} within 7d`}
        />
        <MetricCard label="Recovery rate · C" value="—" sub="no data" note={rateC.note} />
        <MetricCard
          label="Recovered"
          value={formatInr(stats.recovered_paise)}
          sub={`n=${stats.recovered_paise_n}`}
        />
        <MetricCard label="Open episodes" value={stats.open_episodes} sub="episodes that may still act" />
        <MetricCard
          label="False outreach"
          value={m5 === null ? "—" : m5.value}
          action={
            m5 === null ? undefined : m5.value === 0 ? (
              <StatusPill tone="positive">{`${m5.value} · zero by design`}</StatusPill>
            ) : (
              <StatusPill tone="negative">{`${m5.value} detected`}</StatusPill>
            )
          }
          note={m5?.note}
        />
      </div>

      <ProvenanceStrip cohorts={cohorts} />

      <section className="flex flex-col gap-8">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase text-text-muted">Recent activity</h2>
          <Link
            to="/episodes"
            className="text-sm font-medium text-primary hover:text-primary-hover"
          >
            View all
          </Link>
        </div>
        {episodes.error !== null ? (
          <ErrorNote message={episodes.error} />
        ) : episodes.data === null ? (
          <p className="text-sm text-text-muted">Loading episodes…</p>
        ) : (
          <EpisodeTable episodes={episodes.data} />
        )}
      </section>
    </div>
  );
}
