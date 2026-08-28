/**
 * Shared fetch-error state: role="alert", names WHAT failed and the
 * recovery, and a Retry button that re-invokes the same fetch. Replaces
 * every silent blank / bare red line a page used to render on error.
 */

import { cn } from "@/lib/utils";

export function ErrorState({
  message,
  onRetry,
  title = "Something failed to load",
  retryLabel = "Retry",
  className,
}: {
  message: string;
  onRetry: () => void;
  title?: string;
  retryLabel?: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-start gap-12 rounded-card border border-negative-solid bg-negative-bg p-24",
        className,
      )}
    >
      <p className="font-display text-lg font-semibold text-negative-text">{title}</p>
      <p className="max-w-720 text-sm text-negative-text">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="h-control-sm rounded-button border border-negative-solid bg-surface px-16 text-sm font-medium text-negative-text hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {retryLabel}
      </button>
    </div>
  );
}
