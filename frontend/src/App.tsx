import { Navigate, Route, Routes } from "react-router-dom";
import LandingPage from "./pages/Landing";
import RoomPage from "./pages/RoomPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/room/:sessionId" element={<RoomPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
