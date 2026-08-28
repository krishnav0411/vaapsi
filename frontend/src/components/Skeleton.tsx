/**
 * Loading skeletons — the ONLY allowed loading state (no spinners, no
 * blank flashes). A skeleton renders as a token-colored block with a
 * CSS pulse; pages gate it behind useDelayedFlag(300ms) so fast loads
 * never flash. Pure CSS + tokens: no dependencies, no hex literals.
 */

import { cn } from "@/lib/utils";

/** The primitive: a pulsing block sized by width/height props. */
export function Skeleton({
  width,
  height,
  className,
}: {
  width?: number | string;
  height?: number | string;
  className?: string;
}) {
  return (
    <div
      aria-hidden
      style={{ width, height }}
      className={cn(
        "animate-pulse rounded-button bg-border-subtle motion-reduce:animate-none",
        className,
      )}
    />
  );
}

/** N rows × M cols matching the real table's header count. */
export function TableSkeleton({
  rows = 5,
  cols = 6,
  label = "Loading table",
  className,
}: {
  rows?: number;
  cols?: number;
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn(
        "overflow-hidden rounded-card border border-border-subtle bg-surface",
        className,
      )}
    >
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle">
            {Array.from({ length: cols }, (_, col) => (
              <th key={col} className="px-12 py-8">
                <Skeleton height={10} width="70%" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, row) => (
            <tr key={row} className="border-b border-border-subtle last:border-b-0">
              {Array.from({ length: cols }, (_, col) => (
                <td key={col} className="px-12 py-8">
                  <Skeleton height={14} width={col === 0 ? "55%" : "80%"} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The one KPI-card silhouette (label / value / sub lines). */
export function CardSkeleton({
  label = "Loading card",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn(
        "flex flex-col gap-8 rounded-card border border-border-subtle bg-surface p-16",
        className,
      )}
    >
      <Skeleton height={10} width="45%" />
      <Skeleton height={24} width="75%" />
      <Skeleton height={10} width="60%" />
    </div>
  );
}
