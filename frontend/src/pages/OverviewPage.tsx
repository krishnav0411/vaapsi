/**
 * D7.3 Overview — the hero page. Opens on the role's first question
 * (money recovered), then the 5-card KPI band, the cohort provenance
 * strip, and the recent-activity table. All numbers come verbatim from
 * /api/overview (+ /api/metrics for M5, + /api/episodes for the table);
 * the CONTROL rate renders "—" + "no data" when n=0 — never a fake 0%.
 * Loading is skeletons (after the 300ms anti-flash delay), errors are
 * ErrorState with retry, and the headline numbers count up.
 */

import { Link } from "react-router-dom";

import { CountUp } from "@/components/CountUp";
import { EpisodeTable } from "@/components/EpisodeTable";
import { ErrorState } from "@/components/ErrorState";
import { MetricCard } from "@/components/MetricCard";
import { Provenance } from "@/components/Provenance";
import { ProvenanceStrip } from "@/components/ProvenanceStrip";
import { CardSkeleton, TableSkeleton } from "@/components/Skeleton";
import { StatusPill } from "@/components/StatusPill";
import { useDelayedFlag } from "@/hooks/useDelayedFlag";
import {
  useApi,
  type EpisodeRow,
  type Metric,
  type OverviewResponse,
} from "@/lib/api";
import { formatInr, formatPct } from "@/lib/format";

export function OverviewPage() {
  const overview = useApi<OverviewResponse>("/api/overview");
  const episodes = useApi<EpisodeRow[]>("/api/episodes");
  const metrics = useApi<Metric[]>("/api/metrics");

  const showOverviewSkeleton = useDelayedFlag(
    overview.data === null && overview.error === null,
  );
  const showEpisodesSkeleton = useDelayedFlag(
    episodes.data === null && episodes.error === null,
  );

  const { data, error } = overview;
  if (error !== null) {
    return (
      <ErrorState
        title="Couldn't load the overview"
        message="The overview stats failed to load. The API may be restarting — retry."
        onRetry={overview.refetch}
      />
    );
  }
  if (data === null) {
    return showOverviewSkeleton ? (
      <div className="flex flex-col gap-24">
        <div className="grid grid-cols-2 gap-24">
          <CardSkeleton label="Loading recovered total" className="p-24" />
          <CardSkeleton label="Loading recovery rate" className="p-24" />
        </div>
        <div className="grid grid-cols-5 gap-12">
          {Array.from({ length: 5 }, (_, i) => (
            <CardSkeleton key={i} label="Loading stat card" />
          ))}
        </div>
      </div>
    ) : null;
  }

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
          value={
            <CountUp value={stats.recovered_paise} format={formatInr} />
          }
          sub={
            stats.recovered_paise_n === 0
              ? "n=0 — awaiting first payment"
              : `n=${stats.recovered_paise_n}`
          }
          note={stats.recovered_paise_note}
          action={
            <Provenance>
              from the audit ledger, verified row-by-row — every rupee is a
              recovered_paise row in the sha256 chain
            </Provenance>
          }
        />
        <MetricCard
          size="hero"
          label="Recovery rate · treatment"
          value={
            rateT.value === null ? (
              "—"
            ) : (
              <CountUp value={rateT.value * 100} format={(n) => formatPct(n / 100)} />
            )
          }
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
          value={
            rateT.value === null ? (
              "—"
            ) : (
              <CountUp value={rateT.value * 100} format={(n) => formatPct(n / 100)} />
            )
          }
          sub={`${recoveredCount} of ${rateT.n} within 7d`}
        />
        <MetricCard label="Recovery rate · C" value="—" sub="no data" note={rateC.note} />
        <MetricCard
          label="Recovered"
          value={<CountUp value={stats.recovered_paise} format={formatInr} />}
          sub={`n=${stats.recovered_paise_n}`}
        />
        <MetricCard
          label="Open episodes"
          value={<CountUp value={stats.open_episodes} />}
          sub="episodes that may still act"
        />
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
            className="text-sm font-medium text-primary hover:text-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            View all
          </Link>
        </div>
        {episodes.error !== null ? (
          <ErrorState
            title="Couldn't load recent episodes"
            message="The episode list failed to load. The API may be restarting — retry."
            onRetry={episodes.refetch}
          />
        ) : episodes.data === null ? (
          showEpisodesSkeleton ? (
            <TableSkeleton rows={5} cols={6} label="Loading recent episodes" />
          ) : null
        ) : (
          <EpisodeTable episodes={episodes.data} />
        )}
      </section>
    </div>
  );
}
