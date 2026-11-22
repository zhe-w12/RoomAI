import { Navigate, Route, Routes } from "react-router-dom";
import LandingPage from "./pages/Landing";
import RoomPage from "./pages/RoomPage";
import ReplayPage from "./pages/ReplayPage";
import ReportPage from "./pages/ReportPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/room/:sessionId" element={<RoomPage />} />
      <Route path="/room/:sessionId/replay" element={<ReplayPage />} />
      <Route path="/room/:sessionId/memo" element={<ReportPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
