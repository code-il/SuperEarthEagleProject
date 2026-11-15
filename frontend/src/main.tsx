import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./index.css";

const HomePage = lazy(() => import("./pages/HomePage"));
const AboutPage = lazy(() => import("./pages/AboutPage"));
const MapPage = lazy(() => import("./pages/MapPage"));
const ScienceLayout = lazy(() => import("./pages/science/ScienceLayout"));
const ScienceHome = lazy(() => import("./pages/science/ScienceHome"));
const DailyPage = lazy(() => import("./pages/science/DailyPage"));
const LoginPage = lazy(() => import("./pages/science/LoginPage"));
const AdminPage = lazy(() => import("./pages/science/AdminPage"));
const InvestigateList = lazy(() => import("./pages/science/InvestigatePage").then((m) => ({ default: m.InvestigateList })));
const InvestigationPage = lazy(() => import("./pages/science/InvestigatePage").then((m) => ({ default: m.InvestigationPage })));
const ReviewQueue = lazy(() => import("./pages/science/ReviewPage").then((m) => ({ default: m.ReviewQueue })));
const ReviewCandidate = lazy(() => import("./pages/science/ReviewPage").then((m) => ({ default: m.ReviewCandidate })));
const LeaderboardPage = lazy(() => import("./pages/science/BoardsPages").then((m) => ({ default: m.LeaderboardPage })));
const ProgressPage = lazy(() => import("./pages/science/BoardsPages").then((m) => ({ default: m.ProgressPage })));
const ScientificPage = lazy(() => import("./pages/science/BoardsPages").then((m) => ({ default: m.ScientificPage })));
const MyProfile = lazy(() => import("./pages/science/ProfilePage").then((m) => ({ default: m.MyProfile })));
const PlayerProfile = lazy(() => import("./pages/science/ProfilePage").then((m) => ({ default: m.PlayerProfile })));

/** /explore?… → the Atlas (/map) with the Explore pop-up open on that field. */
function Redirect({ explore = false }: { explore?: boolean }) {
  const { search } = useLocation();
  const s = new URLSearchParams(search);
  if (explore) s.set("view", "explore");
  const q = s.toString();
  return <Navigate to={q ? `/map?${q}` : "/map"} replace />;
}

const Loading = () => <div className="p-8 text-sm text-slate-500">Loading…</div>;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/targets" element={<Navigate to="/" replace />} />
          <Route path="/explore" element={<Redirect explore />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="/science" element={<ScienceLayout />}>
            <Route index element={<Suspense fallback={<Loading />}><ScienceHome /></Suspense>} />
            <Route path="daily" element={<Suspense fallback={<Loading />}><DailyPage /></Suspense>} />
            <Route path="login" element={<Suspense fallback={<Loading />}><LoginPage /></Suspense>} />
            <Route path="investigate" element={<Suspense fallback={<Loading />}><InvestigateList /></Suspense>} />
            <Route path="investigate/:id" element={<Suspense fallback={<Loading />}><InvestigationPage /></Suspense>} />
            <Route path="review" element={<Suspense fallback={<Loading />}><ReviewQueue /></Suspense>} />
            <Route path="review/:id" element={<Suspense fallback={<Loading />}><ReviewCandidate /></Suspense>} />
            <Route path="leaderboard" element={<Suspense fallback={<Loading />}><LeaderboardPage /></Suspense>} />
            <Route path="profile" element={<Suspense fallback={<Loading />}><MyProfile /></Suspense>} />
            <Route path="players/:id" element={<Suspense fallback={<Loading />}><PlayerProfile /></Suspense>} />
            <Route path="progress" element={<Suspense fallback={<Loading />}><ProgressPage /></Suspense>} />
            <Route path="contributions" element={<Suspense fallback={<Loading />}><ScientificPage /></Suspense>} />
            <Route path="admin" element={<Suspense fallback={<Loading />}><AdminPage /></Suspense>} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
);
