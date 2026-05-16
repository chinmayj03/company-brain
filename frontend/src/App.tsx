import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Brain, GitBranch, MessageSquareText, Network, Radar } from "lucide-react";
import RepoOverview from "./screens/RepoOverview";
import BrainBrowser from "./screens/BrainBrowser";
import QueryConsole from "./screens/QueryConsole";
import DriftDashboard from "./screens/DriftDashboard";

const nav = [
  { to: "/repos", label: "Repos", Icon: GitBranch },
  { to: "/browser", label: "Brain Browser", Icon: Network },
  { to: "/query", label: "Query", Icon: MessageSquareText },
  { to: "/drift", label: "Drift", Icon: Radar },
];

export default function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Brain size={19} /></span>
          <span>
            <strong>Company Brain</strong>
            <small>local demo</small>
          </span>
        </div>
        <nav>
          {nav.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="dot live" />
          Single-user demo mode
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/repos" replace />} />
          <Route path="/repos" element={<RepoOverview />} />
          <Route path="/browser" element={<BrainBrowser />} />
          <Route path="/query" element={<QueryConsole />} />
          <Route path="/drift" element={<DriftDashboard />} />
        </Routes>
      </main>
    </div>
  );
}
