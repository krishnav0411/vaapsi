/**
 * Full-width alert strip under the AppBar for KILLED/DEGRADED, and the
 * public-demo chip when the deployment runs read-only (VAAPSI_PUBLIC_DEMO).
 * NORMAL without demo renders nothing — by design, matching the Jinja
 * dashboard's behavior. Banner copy mirrors the Jinja kill-switch/degraded
 * wording so both dashboards tell the same truth; the demo chip is a
 * notice-tone token pill (never an error color — a demo is not an alarm).
 */

import type { Mode } from "@/lib/api";

export function ModeBanner({
  mode,
  demo = false,
}: {
  mode: Mode | null;
  demo?: boolean;
}) {
  const alert =
    mode === null || mode === "NORMAL" ? null : (
      <div
        role="alert"
        className={
          mode === "KILLED"
            ? "bg-negative-bg px-48 py-8 text-sm font-medium text-negative-text"
            : "bg-notice-bg px-48 py-8 text-sm font-medium text-notice-text"
        }
      >
        <div className="mx-auto w-full max-w-content-max">
          {mode === "KILLED"
            ? "KILLED — outreach, approvals and all outbound Razorpay calls are refused. One-way until a process restart."
            : "DEGRADED — the newest ledger row announces an LLM outage; outbound actions may be refused."}
        </div>
      </div>
    );

  if (!demo) return alert;

  return (
    <>
      <div
        role="status"
        className="bg-notice-bg px-48 py-8 text-sm font-medium text-notice-text"
      >
        <div className="mx-auto flex w-full max-w-content-max flex-wrap items-center gap-12">
          <span className="inline-flex items-center rounded-pill border border-notice-solid px-12 py-2 text-xs font-semibold uppercase tracking-wide">
            PUBLIC DEMO — read only
          </span>
          <span className="text-text-subtle">
            Writes are disabled on this deployment; everything below is seeded,
            sanitized demo data.
          </span>
        </div>
      </div>
      {alert}
    </>
  );
}
