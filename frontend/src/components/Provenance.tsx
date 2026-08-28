/**
 * Provenance tooltip: a small ⓘ button (aria-label "explain this
 * number") that reveals the number's derivation on hover OR keyboard
 * focus — pure CSS group-hover + focus-within, token-styled, zero deps.
 * Copy is truthful: each tooltip states the metric's real query
 * semantics (app/dashboard/metrics.py), never a marketing gloss.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Provenance({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group relative inline-flex items-center", className)}>
      <button
        type="button"
        aria-label="explain this number"
        className="flex h-16 w-16 items-center justify-center rounded-pill text-text-muted hover:text-text-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <svg
          viewBox="0 0 16 16"
          width="14"
          height="14"
          aria-hidden
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
        >
          <circle cx="8" cy="8" r="6.4" />
          <line x1="8" y1="7.2" x2="8" y2="11.4" strokeLinecap="round" />
          <circle cx="8" cy="4.7" r="0.9" fill="currentColor" stroke="none" />
        </svg>
      </button>
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-4 w-max max-w-280 -translate-x-1/2 rounded-button border border-border-subtle bg-surface px-12 py-8 text-xs text-text-subtle opacity-0 shadow-high transition-opacity duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}
