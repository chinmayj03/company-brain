import { Link } from "react-router-dom";
import { ChevronDown, FileText } from "lucide-react";
import type { Citation } from "../api/types";

export default function CitationsPanel({ citations }: { citations: Citation[] }) {
  if (!citations.length) {
    return <div className="empty-state">No citations returned for this answer.</div>;
  }

  return (
    <div className="citation-list">
      {citations.map((citation, index) => (
        <details className="citation" key={`${citation.urn}-${index}`} open={index === 0}>
          <summary>
            <span className="icon-chip"><FileText size={14} /></span>
            <span>
              <strong>{citation.name || citation.urn}</strong>
              <small>{citation.file || citation.urn}</small>
            </span>
            <ChevronDown size={15} />
          </summary>
          <p>{citation.why_relevant || "Referenced by the query response."}</p>
          <div className="citation-actions">
            <Link to={`/browser?urn=${encodeURIComponent(citation.urn)}`}>Open entity</Link>
            {citation.confidence !== undefined && <span>{Math.round(citation.confidence * 100)}% confidence</span>}
          </div>
        </details>
      ))}
    </div>
  );
}
