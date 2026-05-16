import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import type { BrainEntity } from "../api/types";
import CitationsPanel from "./CitationsPanel";

export default function EntityCard({ entity }: { entity: BrainEntity }) {
  return (
    <article className="entity-card">
      <div className="entity-head">
        <div>
          <span className="pill">{entity.type}</span>
          <h2>{entity.name}</h2>
        </div>
        <span className={`risk risk-${entity.risk.toLowerCase()}`}>{entity.risk}</span>
      </div>
      <p className="summary">{entity.summary || "No synthesized summary yet."}</p>
      <dl className="meta-grid">
        <div>
          <dt>Role</dt>
          <dd>{entity.role || "Unknown"}</dd>
        </div>
        <div>
          <dt>File</dt>
          <dd>{entity.file || "n/a"}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{entity.last_updated ? new Date(entity.last_updated).toLocaleString() : "n/a"}</dd>
        </div>
        <div>
          <dt>Edges</dt>
          <dd>{entity.edges.length}</dd>
        </div>
      </dl>
      {entity.related_entities?.length ? (
        <section>
          <h3>Related</h3>
          <div className="related-list">
            {entity.related_entities.slice(0, 8).map((related) => (
              <Link key={related.urn} to={`/browser?urn=${encodeURIComponent(related.urn)}`}>
                {related.name}
                <ExternalLink size={12} />
              </Link>
            ))}
          </div>
        </section>
      ) : null}
      <section>
        <h3>Provenance</h3>
        <CitationsPanel citations={entity.citations} />
      </section>
    </article>
  );
}
