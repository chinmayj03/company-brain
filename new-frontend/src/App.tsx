import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Ask from './views/Ask';
import History from './views/History';
import Saved from './views/Saved';
import AgentsMCP from './views/AgentsMCP';
import AuditLog from './views/AuditLog';
import Sources from './views/Sources';
import FlagOverlay from './components/FlagOverlay';
import LiveModeChip from './components/LiveModeChip';
import { useWorkspaceBootstrap } from './hooks/useWorkspace';

function AppInner() {
  useWorkspaceBootstrap();
  return (
    <>
      <Routes>
        <Route path="/"        element={<Navigate to="/ask" replace />} />
        <Route path="/ask"     element={<Ask />} />
        <Route path="/history" element={<History />} />
        <Route path="/saved"   element={<Saved />} />
        <Route path="/agents"  element={<AgentsMCP />} />
        <Route path="/audit"   element={<AuditLog />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="*"        element={<Navigate to="/ask" replace />} />
      </Routes>
      <FlagOverlay />
      <LiveModeChip />
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppInner />
    </BrowserRouter>
  );
}
