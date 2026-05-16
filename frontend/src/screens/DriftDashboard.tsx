import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { brainClient } from "../api/brain_client";
import type { DriftItem } from "../api/types";

export default function DriftDashboard() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["drift", "latest"],
    queryFn: brainClient.latestDrift,
  });
  const selected = useMemo<DriftItem | undefined>(
    () => data?.items.find((item) => item.id === selectedId) || data?.items[0],
    [data?.items, selectedId],
  );

  return (
    <section className="page" data-testid="drift-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">VP Eng lens</p>
          <h1>Drift Dashboard</h1>
        </div>
        {data?.mock && <span className="mock-banner">MOCK DATA until ADR-0082 ships</span>}
      </header>

      {isLoading && <div className="panel">Loading drift snapshot...</div>}
      {error && <div className="alert">Could not load drift: {(error as Error).message}</div>}

      {data && (
        <>
          <div className="domain-grid">
            {data.domains.map((domain) => (
              <article className="domain-card" key={domain.domain}>
                <span className={`severity severity-${domain.severity}`}>{domain.severity}</span>
                <strong>{domain.domain}</strong>
                <span>{domain.count} open items</span>
              </article>
            ))}
          </div>

          <div className="drift-layout">
            <div className="panel">
              <h2>Open Drift Items</h2>
              <div className="drift-list">
                {data.items.map((item) => (
                  <button
                    key={item.id}
                    className={`drift-row ${item.id === selected?.id ? "active" : ""}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <AlertTriangle size={15} />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.domain} - {item.state}</small>
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <div className="panel">
              <h2>Item Detail</h2>
              {selected ? (
                <div className="drift-detail">
                  <span className={`severity severity-${selected.severity}`}>{selected.severity}</span>
                  <h3>{selected.title}</h3>
                  <p>State: {selected.state}</p>
                  {selected.entity_urn && (
                    <Link to={`/browser?urn=${encodeURIComponent(selected.entity_urn)}`}>Open related entity</Link>
                  )}
                  <h4>History</h4>
                  <ol>
                    {selected.history.map((entry, index) => (
                      <li key={index}>
                        <strong>{entry.at ? new Date(entry.at).toLocaleString() : "Snapshot"}</strong>
                        <span>{entry.event}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : (
                <div className="empty-state">No drift items in this snapshot.</div>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
