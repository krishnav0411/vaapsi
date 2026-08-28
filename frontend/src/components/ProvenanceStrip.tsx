/**
 * The provenance line both data pages share (Overview + Metrics): the
 * pre-registered cohort sizes and how assignment happened. One component
 * so the two surfaces can never drift apart on the honesty strip.
 */

import type { Cohort } from "@/lib/api";

export function ProvenanceStrip({
  cohorts,
}: {
  cohorts: Partial<Record<Cohort, number>> | undefined;
}) {
  return (
    <p className="text-xs text-text-muted">
      Cohorts {cohorts?.TREATMENT ?? 0} TREATMENT / {cohorts?.CONTROL ?? 0} CONTROL — interleaved
      at creation; assignment recorded at creation, never post-hoc.
    </p>
  );
}
