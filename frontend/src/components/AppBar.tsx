/**
 * Top AppBar: current page title (TASA Orbiter 600, 24px) on the left;
 * on the right the data-freshness readout ("updated Ns ago" + health
 * dot, off the shared useFreshness clock: green pulse <60s, amber
 * 60–300s, red stale >300s), the mode badge (NORMAL neutral / DEGRADED
 * notice / KILLED negative), the static TEST MODE outline tag, the ⌘K
 * command-palette button (opens the shell-level CommandPalette) and the
 * theme toggle. Title follows the URL, not prop plumbing, so the shell
 * stays a single source of chrome truth.
 */

import { useLocation } from "react-router-dom";

import { ModePill } from "@/components/StatusPill";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useFreshness } from "@/hooks/useFreshness";
import type { Mode } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

function pageTitle(pathname: string): string {
  if (pathname === "/episodes") return "Episodes";
  if (pathname.startsWith("/episodes/")) return "Episode detail";
  if (pathname === "/metrics") return "Metrics";
  if (pathname === "/ledger") return "Ledger";
  if (pathname === "/drills") return "Drills";
  if (pathname === "/approvals") return "Approvals";
  return "Overview";
}

function FreshnessChip() {
  const { lastSuccessMs, secondsAgo, status } = useFreshness();
  const dotClass =
    status === "fresh"
      ? "bg-positive-solid animate-pulse"
      : status === "aging"
        ? "bg-notice-solid"
        : status === "stale"
          ? "bg-negative-solid"
          : "bg-border-normal";
  return (
    <span
      className="flex items-center gap-8"
      title="time since the last successful /health poll"
    >
      {secondsAgo !== null && (
        <span className="tnum hidden text-xs text-text-muted sm:inline">
          updated {timeAgo(new Date(lastSuccessMs ?? 0).toISOString())}
        </span>
      )}
      <span aria-hidden className={cn("h-8 w-8 shrink-0 rounded-pill", dotClass)} />
      <span className="sr-only">
        {secondsAgo === null
          ? "data freshness unknown"
          : `data ${status}: last success ${secondsAgo} seconds ago`}
      </span>
    </span>
  );
}

function PaletteButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      aria-label="Open command palette"
      title="Command palette (⌘K / Ctrl+K)"
      onClick={onOpen}
      className="hidden h-control-sm items-center rounded-button border border-border-normal px-8 font-mono text-xs text-text-subtle hover:border-border-hover hover:bg-row-hover sm:flex"
    >
      ⌘K
    </button>
  );
}

export function AppBar({
  mode,
  onOpenPalette,
}: {
  mode: Mode | null;
  onOpenPalette: () => void;
}) {
  const { pathname } = useLocation();
  return (
    <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface">
      <div className="mx-auto flex h-control-lg w-full max-w-content-max items-center justify-between gap-16 px-48 pl-56 lg:pl-48">
        <h1 className="font-display text-2xl font-semibold text-text-normal">
          {pageTitle(pathname)}
        </h1>
        <div className="flex items-center gap-8">
          <FreshnessChip />
          {mode !== null && <ModePill mode={mode} />}
          <span className="rounded-pill border border-primary px-12 py-2 text-xs font-medium text-primary">
            TEST MODE
          </span>
          <PaletteButton onOpen={onOpenPalette} />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
