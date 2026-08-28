/**
 * The one KPI card shape (Blade law): muted uppercase label, big tabular
 * value, optional sub/note. Deltas and pills are the CALLER's job and
 * only render when the caller has real math — this component never
 * invents arrows or sparklines.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function MetricCard({
  label,
  value,
  sub,
  note,
  action,
  size = "kpi",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  note?: ReactNode;
  action?: ReactNode;
  size?: "kpi" | "hero";
}) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-card border border-border-subtle bg-surface shadow-low",
        size === "hero" ? "gap-8 p-24" : "gap-4 p-16",
      )}
    >
      <div className="flex items-start justify-between gap-8">
        <p className="text-xs font-medium uppercase text-text-muted">{label}</p>
        {action}
      </div>
      <p
        className={cn(
          "tnum font-semibold text-text-normal",
          size === "hero" ? "text-hero" : "text-2xl",
        )}
      >
        {value}
      </p>
      {sub !== undefined && <p className="text-sm text-text-subtle">{sub}</p>}
      {note !== undefined && <p className="text-xs text-text-muted">{note}</p>}
    </div>
  );
}
