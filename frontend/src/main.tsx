import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./index.css";

const HomePage = lazy(() => import("./pages/HomePage"));
const AboutPage = lazy(() => import("./pages/AboutPage"));
const MapPage = lazy(() => import("./pages/MapPage"));

/** Old links: /map → the Atlas; /explore?… → the Atlas with the Explore pop-up open on that field. */
function Redirect({ explore = false }: { explore?: boolean }) {
  const { search } = useLocation();
  const s = new URLSearchParams(search);
  if (explore) s.set("view", "explore");
  const q = s.toString();
  return <Navigate to={q ? `/?${q}` : "/"} replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={<div className="p-8 text-slate-400">Loading…</div>}>
        <Routes>
          <Route path="/" element={<MapPage />} />
          <Route path="/targets" element={<HomePage />} />
          <Route path="/explore" element={<Redirect explore />} />
          <Route path="/map" element={<Redirect />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
);
