/**
 * The D7.2 app shell: fixed 256px sidebar (docked on lg+, off-canvas
 * behind a toggle on small viewports), AppBar and mode banner over a
 * max-w-1200 content column on the canvas. /api/mode is fetched once
 * here and refetched after a kill-switch 200, so the AppBar badge, the
 * sidebar readout and the banner can never disagree. Unknown URLs
 * redirect to Overview. The ⌘K CommandPalette mounts at shell level so
 * the shortcut and its overlay work from any route.
 */

import { useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppBar } from "@/components/AppBar";
import { CommandPalette } from "@/components/CommandPalette";
import { ModeBanner } from "@/components/ModeBanner";
import { Sidebar } from "@/components/Sidebar";
import { useApi, type ModeResponse } from "@/lib/api";
import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { DrillsPage } from "@/pages/DrillsPage";
import { EpisodesPage } from "@/pages/EpisodesPage";
import { EpisodeDetailPage } from "@/pages/EpisodeDetailPage";
import { LedgerPage } from "@/pages/LedgerPage";
import { MetricsPage } from "@/pages/MetricsPage";
import { OverviewPage } from "@/pages/OverviewPage";

export default function App() {
  const mode = useApi<ModeResponse>("/api/mode");
  const currentMode = mode.data?.mode ?? null;
  const demoMode = mode.data?.demo === true;
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <div data-testid="app-ready" className="min-h-screen bg-canvas font-sans text-text-normal">
      <Sidebar mode={currentMode} onKilled={mode.refetch} />
      <div className="pl-sidebar max-lg:pl-0">
        <AppBar mode={currentMode} onOpenPalette={() => setPaletteOpen(true)} />
        <ModeBanner mode={currentMode} demo={demoMode} />
        <main className="mx-auto w-full max-w-content-max px-48 py-32 max-lg:px-24">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/episodes" element={<EpisodesPage />} />
            <Route path="/episodes/:id" element={<EpisodeDetailPage />} />
            <Route path="/metrics" element={<MetricsPage />} />
            <Route path="/ledger" element={<LedgerPage />} />
            <Route path="/drills" element={<DrillsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
