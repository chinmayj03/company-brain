import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Ask from './views/Ask';
import FlagOverlay from './components/FlagOverlay';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/"    element={<Navigate to="/ask" replace />} />
        <Route path="/ask" element={<Ask />} />
        {/* Phase B stub routes — Codebase, Trace, PushFlow land in next session */}
        <Route path="*"    element={<Navigate to="/ask" replace />} />
      </Routes>
      <FlagOverlay />
    </BrowserRouter>
  );
}
