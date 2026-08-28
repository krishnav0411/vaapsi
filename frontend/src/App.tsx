/**
 * The D7.2 app shell: fixed 256px sidebar (docked on lg+, off-canvas
 * behind a toggle on small viewports), AppBar and mode banner over a
 * max-w-1200 content column on the canvas. /api/mode is fetched once
 * here and refetched after a kill-switch 200, so the AppBar badge, the
 * sidebar readout and the banner can never disagree. Unknown URLs
 * redirect to Overview.
 */

import { Navigate, Route, Routes } from "react-router-dom";

import { AppBar } from "@/components/AppBar";
import { ModeBanner } from "@/components/ModeBanner";
import { Sidebar } from "@/components/Sidebar";
import { useApi, type ModeResponse } from "@/lib/api";
import { EpisodesPage } from "@/pages/EpisodesPage";
import { EpisodeDetailPage } from "@/pages/EpisodeDetailPage";
import { MetricsPage } from "@/pages/MetricsPage";
import { OverviewPage } from "@/pages/OverviewPage";

export default function App() {
  const mode = useApi<ModeResponse>("/api/mode");
  const currentMode = mode.data?.mode ?? null;

  return (
    <div className="min-h-screen bg-canvas font-sans text-text-normal">
      <Sidebar mode={currentMode} onKilled={mode.refetch} />
      <div className="pl-sidebar max-lg:pl-0">
        <AppBar mode={currentMode} />
        <ModeBanner mode={currentMode} />
        <main className="mx-auto w-full max-w-content-max px-48 py-32 max-lg:px-24">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/episodes" element={<EpisodesPage />} />
            <Route path="/episodes/:id" element={<EpisodeDetailPage />} />
            <Route path="/metrics" element={<MetricsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
