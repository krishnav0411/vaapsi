/**
 * Blade status pills — the single pill shape for modes, cohorts and
 * episode states. Tone mapping follows Blade's law:
 * NEW/VOIDED/CLOSED → neutral, DIAGNOSED/SCORED/GATED → information,
 * SENT → information with a solid dot, VERIFIED → positive;
 * NORMAL → neutral, DEGRADED → notice, KILLED → negative.
 */

import type { ReactNode } from "react";

import type { EpisodeState, Mode } from "@/lib/api";
import { cn } from "@/lib/utils";

export type PillTone = "positive" | "negative" | "notice" | "information" | "neutral";

const toneClasses: Record<PillTone, string> = {
  positive: "bg-positive-bg text-positive-text",
  negative: "bg-negative-bg text-negative-text",
  notice: "bg-notice-bg text-notice-text",
  information: "bg-info-bg text-info-text",
  neutral: "bg-neutral-bg text-neutral-text",
};

const solidDotClasses: Record<PillTone, string> = {
  positive: "bg-positive-solid",
  negative: "bg-negative-solid",
  notice: "bg-notice-solid",
  information: "bg-info-solid",
  neutral: "bg-neutral-text",
};

export function StatusPill({
  tone,
  dot = false,
  children,
}: {
  tone: PillTone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-4 rounded-pill px-12 py-2 text-xs font-medium",
        toneClasses[tone],
      )}
    >
      {dot && <span aria-hidden className={cn("h-8 w-8 rounded-pill", solidDotClasses[tone])} />}
      {children}
    </span>
  );
}

export function EpisodeStatePill({ state }: { state: EpisodeState }) {
  const tone: PillTone =
    state === "VERIFIED"
      ? "positive"
      : state === "DIAGNOSED" || state === "SCORED" || state === "GATED" || state === "SENT"
        ? "information"
        : "neutral";
  return (
    <StatusPill tone={tone} dot={state === "SENT"}>
      {state}
    </StatusPill>
  );
}

export function ModePill({ mode }: { mode: Mode }) {
  const tone: PillTone =
    mode === "KILLED" ? "negative" : mode === "DEGRADED" ? "notice" : "neutral";
  return <StatusPill tone={tone}>{mode}</StatusPill>;
}
