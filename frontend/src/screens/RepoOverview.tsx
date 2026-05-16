import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Database, Play, RefreshCcw } from "lucide-react";
import { brainClient } from "../api/brain_client";

export default function RepoOverview() {
  const { data: repos = [], isLoading, error } = useQuery({
    queryKey: ["repos"],
    queryFn: brainClient.listRepos,
  });

  return (
    <section className="page" data-testid="repos-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Seed demo</p>
          <h1>Repos Overview</h1>
        </div>
        <button className="secondary" disabled>
          <RefreshCcw size={16} />
          Re-extract
        </button>
      </header>

      {isLoading && <div className="panel">Loading connected repos...</div>}
      {error && <div className="alert">Could not load repos: {(error as Error).message}</div>}

      <div className="table-panel">
        <table>
          <thead>
            <tr>
              <th>Repo</th>
              <th>Status</th>
              <th>Entities</th>
              <th>Edges</th>
              <th>Last extracted</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {repos.map((repo) => (
              <tr key={repo.id}>
                <td>
                  <div className="repo-name">
                    <Database size={16} />
                    <span>
                      <strong>{repo.name}</strong>
                      <small>{repo.path}</small>
                    </span>
                  </div>
                </td>
                <td><span className={`status status-${repo.status}`}>{repo.status}</span></td>
                <td>{repo.entity_count.toLocaleString()}</td>
                <td>{repo.edge_count.toLocaleString()}</td>
                <td>{repo.last_extracted ? new Date(repo.last_extracted).toLocaleString() : "n/a"}</td>
                <td className="row-actions">
                  <button className="icon-only" disabled title="Re-extract">
                    <Play size={15} />
                  </button>
                  <Link className="button-link" to={`/browser?repo=${encodeURIComponent(repo.id)}`}>Open Brain Browser</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
