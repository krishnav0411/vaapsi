/**
 * Full-width alert strip under the AppBar for KILLED/DEGRADED. NORMAL
 * renders nothing — by design, matching the Jinja dashboard's behavior.
 * Banner copy mirrors the Jinja kill-switch/degraded wording so both
 * dashboards tell the same truth.
 */

import type { Mode } from "@/lib/api";

export function ModeBanner({ mode }: { mode: Mode | null }) {
  if (mode === null || mode === "NORMAL") return null;

  const killed = mode === "KILLED";
  return (
    <div
      role="alert"
      className={
        killed
          ? "bg-negative-bg px-48 py-8 text-sm font-medium text-negative-text"
          : "bg-notice-bg px-48 py-8 text-sm font-medium text-notice-text"
      }
    >
      <div className="mx-auto w-full max-w-content-max">
        {killed
          ? "KILLED — outreach, approvals and all outbound Razorpay calls are refused. One-way until a process restart."
          : "DEGRADED — the newest ledger row announces an LLM outage; outbound actions may be refused."}
      </div>
    </div>
  );
}
