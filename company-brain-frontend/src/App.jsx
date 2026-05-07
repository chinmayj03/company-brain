import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Brain, GitBranch, Search, Zap, Network, FileCode2, Database } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import ApiExplorer from './pages/ApiExplorer';
import QueryPage from './pages/QueryPage';
import Architecture from './pages/Architecture';
import ContractBrowserPage from './pages/ContractBrowserPage';
import SchemaExplorerPage from './pages/SchemaExplorerPage';
import clsx from 'clsx';

const NAV = [
  { to: '/dashboard',     label: 'Service Map',  Icon: GitBranch  },
  { to: '/explore',       label: 'API Explorer', Icon: Search     },
  { to: '/query',         label: 'Ask',          Icon: Zap        },
  { to: '/architecture',  label: 'Architecture', Icon: Network    },
  { to: '/contracts',     label: 'Contracts',    Icon: FileCode2  },
  { to: '/schema',        label: 'Schema',       Icon: Database   },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden">
        {/* Sidebar */}
        <aside className="w-56 flex-shrink-0 bg-slate-900 text-white flex flex-col">
          {/* Logo */}
          <div className="flex items-center gap-2 px-5 py-5 border-b border-slate-700">
            <Brain size={22} className="text-brand-500" />
            <span className="font-semibold text-sm tracking-wide">Company Brain</span>
          </div>

          {/* Nav */}
          <nav className="flex-1 px-3 py-4 flex flex-col gap-1">
            {NAV.map(({ to, label, Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  clsx(
                    'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                    isActive
                      ? 'bg-brand-600 text-white'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800'
                  )
                }
              >
                <Icon size={16} />
                {label}
              </NavLink>
            ))}
          </nav>

          {/* Provider badge */}
          <div className="px-5 py-4 border-t border-slate-700">
            <ProviderBadge />
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/explore" element={<ApiExplorer />} />
            <Route path="/query" element={<QueryPage />} />
            <Route path="/architecture" element={<Architecture />} />
            <Route path="/contracts"   element={<ContractBrowserPage />} />
            <Route path="/schema"      element={<SchemaExplorerPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function ProviderBadge() {
  const { data } = useHealth();
  if (!data) return null;
  const colors = { ollama: 'bg-emerald-700', anthropic: 'bg-purple-700', openai: 'bg-blue-700' };
  return (
    <div className={clsx('text-xs px-2 py-1 rounded text-white font-medium w-fit', colors[data.llm_provider] || 'bg-slate-700')}>
      {data.llm_provider}
    </div>
  );
}

function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => fetch('/ai/health').then(r => r.json()),
    refetchInterval: 30_000,
  });
}
