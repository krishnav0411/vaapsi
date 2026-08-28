/**
 * Fixed 256px sidebar (layout law): Vaapsi wordmark in TASA Orbiter with
 * its tagline, the live nav items with active azure + tint + 2px left
 * indicator, and the bottom block carrying the live mode readout and the
 * kill switch (destructive outline). Nav state follows the router so the
 * highlight can never drift from the URL. The D8 surfaces (Ledger,
 * Drills, Approvals) are live routes, so they are first-class NavLinks.
 * On small viewports the sidebar slides off-canvas behind a fixed toggle
 * button (plain Tailwind, no new deps; on lg+ it is permanently docked).
 */

import { useState } from "react";
import {
  BarChart3,
  BookOpen,
  ClipboardCheck,
  Home,
  List,
  Menu,
  Repeat,
  X,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { KillSwitchDialog } from "@/components/KillSwitchDialog";
import { ModePill } from "@/components/StatusPill";
import type { Mode } from "@/lib/api";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Overview", icon: Home, end: true },
  { to: "/episodes", label: "Episodes", icon: List, end: true },
  { to: "/metrics", label: "Metrics", icon: BarChart3, end: true },
  { to: "/ledger", label: "Ledger", icon: BookOpen, end: true },
  { to: "/drills", label: "Drills", icon: Repeat, end: true },
  { to: "/approvals", label: "Approvals", icon: ClipboardCheck, end: true },
];

export function Sidebar({ mode, onKilled }: { mode: Mode | null; onKilled: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={open ? "Close navigation" : "Open navigation"}
        className="fixed left-16 top-8 z-50 flex h-control-sm w-control-sm items-center justify-center rounded-button border border-border-subtle bg-surface text-text-subtle lg:hidden"
      >
        {open ? <X className="h-16 w-16" aria-hidden /> : <Menu className="h-16 w-16" aria-hidden />}
      </button>
      {open && (
        <div
          aria-hidden
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-30 bg-text-normal/40 lg:hidden"
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-sidebar flex-col border-r border-border-subtle bg-surface",
          "transition-transform duration-200 motion-reduce:transition-none",
          open ? "max-lg:translate-x-0" : "max-lg:invisible max-lg:-translate-x-full",
        )}
      >
        <div className="px-24 pb-16 pt-24 max-lg:pl-56">
          <p className="font-display text-xl font-semibold text-text-normal">Vaapsi</p>
          <p className="mt-4 text-xs text-text-muted">Bounded subscription recovery</p>
        </div>
        <nav className="flex-1 px-16">
          <ul className="flex flex-col gap-4">
            {navItems.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      "relative flex h-control-md items-center gap-8 rounded-button px-16 text-sm font-medium",
                      "hover:bg-row-hover-strong",
                      isActive ? "bg-primary-tint text-primary" : "text-text-subtle",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <span aria-hidden className="absolute inset-y-0 left-0 w-2 bg-primary" />
                      )}
                      <Icon className="h-16 w-16" aria-hidden />
                      {label}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="border-t border-border-subtle px-24 py-16">
          <p className="text-xs font-medium uppercase text-text-muted">Mode</p>
          <div className="mt-8">
            {mode === null ? (
              <span className="text-sm text-text-disabled">…</span>
            ) : (
              <ModePill mode={mode} />
            )}
          </div>
          <div className="mt-16">
            <KillSwitchDialog onKilled={onKilled} />
          </div>
        </div>
      </aside>
    </>
  );
}
