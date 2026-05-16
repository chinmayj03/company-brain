import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { brainClient } from "../api/brain_client";
import EntityCard from "../components/EntityCard";
import EdgeGraph from "../components/EdgeGraph";

export default function BrainBrowser() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [type, setType] = useState("all");
  const selectedUrn = searchParams.get("urn");

  const listQuery = useQuery({
    queryKey: ["entities", q, type],
    queryFn: () => brainClient.listEntities({ q, type, page_size: 40 }),
  });

  const firstUrn = listQuery.data?.items[0]?.urn;
  const activeUrn = selectedUrn || firstUrn;

  const detailQuery = useQuery({
    queryKey: ["entity", activeUrn],
    queryFn: () => brainClient.entityDetail(activeUrn!),
    enabled: Boolean(activeUrn),
  });

  useEffect(() => {
    if (!selectedUrn && firstUrn) {
      setSearchParams({ urn: firstUrn }, { replace: true });
    }
  }, [firstUrn, selectedUrn, setSearchParams]);

  const types = useMemo(() => listQuery.data?.types || [], [listQuery.data?.types]);

  return (
    <section className="page browser-page" data-testid="browser-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Entity graph</p>
          <h1>Brain Browser</h1>
        </div>
      </header>

      <div className="browser-layout">
        <aside className="browser-list">
          <div className="filters">
            <label className="searchbox">
              <Search size={15} />
              <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="Filter entities" />
            </label>
            <label className="field">
              <span>Type</span>
              <select value={type} onChange={(event) => setType(event.target.value)}>
                <option value="all">All types</option>
                {types.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          </div>
          <div className="entity-list">
            {listQuery.isLoading && <div className="empty-state">Loading entities...</div>}
            {listQuery.data?.items.map((entity) => (
              <button
                key={entity.urn}
                className={`entity-row ${entity.urn === activeUrn ? "active" : ""}`}
                onClick={() => setSearchParams({ urn: entity.urn })}
              >
                <strong>{entity.name}</strong>
                <span>{entity.type}</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="browser-detail">
          {detailQuery.isLoading && <div className="panel">Loading entity...</div>}
          {detailQuery.error && <div className="alert">Could not load entity: {(detailQuery.error as Error).message}</div>}
          {detailQuery.data && (
            <>
              <EntityCard entity={detailQuery.data} />
              <section className="panel">
                <h3>Edges</h3>
                <EdgeGraph entity={detailQuery.data} />
              </section>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
