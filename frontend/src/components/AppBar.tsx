/**
 * Top AppBar: current page title (TASA Orbiter 600, 24px) on the left;
 * mode Badge (NORMAL neutral / DEGRADED notice / KILLED negative) and the
 * static TEST MODE outline tag on the right. Title follows the URL, not
 * prop plumbing, so the shell stays a single source of chrome truth.
 */

import { useLocation } from "react-router-dom";

import { ModePill } from "@/components/StatusPill";
import type { Mode } from "@/lib/api";

function pageTitle(pathname: string): string {
  if (pathname === "/episodes") return "Episodes";
  if (pathname.startsWith("/episodes/")) return "Episode detail";
  if (pathname === "/metrics") return "Metrics";
  return "Overview";
}

export function AppBar({ mode }: { mode: Mode | null }) {
  const { pathname } = useLocation();
  return (
    <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface">
      <div className="mx-auto flex h-control-lg w-full max-w-content-max items-center justify-between px-48">
        <h1 className="font-display text-2xl font-semibold text-text-normal">
          {pageTitle(pathname)}
        </h1>
        <div className="flex items-center gap-8">
          {mode !== null && <ModePill mode={mode} />}
          <span className="rounded-pill border border-primary px-12 py-2 text-xs font-medium text-primary">
            TEST MODE
          </span>
        </div>
      </div>
    </header>
  );
}
