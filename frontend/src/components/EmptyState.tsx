/**
 * Shared empty state: a calm icon area (inline SVG only), one line of
 * explanation, no fake data. Used wherever a collection is legitimately
 * empty so silence never reads as a bug.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function EmptyState({
  icon,
  title,
  explanation,
  className,
}: {
  icon: ReactNode;
  title: string;
  explanation: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-8 rounded-card border border-border-subtle bg-surface p-40 text-center shadow-low",
        className,
      )}
    >
      <div aria-hidden className="flex h-40 w-40 items-center justify-center rounded-pill bg-canvas text-text-muted">
        {icon}
      </div>
      <p className="font-display text-lg font-semibold text-text-normal">{title}</p>
      <p className="max-w-480 text-sm text-text-muted">{explanation}</p>
    </div>
  );
}
