/**
 * CommitTimeline
 *
 * Renders the ordered commit history for an API endpoint across repos.
 * Engineers can expand each cluster to see the diff and add an annotation.
 *
 * Design: annotations are anchored to specific commits (not free text).
 * See PIPELINE-api-context-builder.md Section 4 for the full rationale.
 */

import { useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import { GitCommit, ChevronDown, ChevronRight, PenLine, CheckCircle, AlertTriangle } from 'lucide-react';
import { addAnnotation } from '../../api/client';
import AnnotationEditor from './AnnotationEditor';

export default function CommitTimeline({ nodeId, clusters, onAnnotationSaved }) {
  const [expanded, setExpanded] = useState({});
  const [annotating, setAnnotating] = useState(null); // cluster_id being annotated

  const toggle = (id) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const handleSaveAnnotation = async (clusterId, annotation) => {
    await addAnnotation(nodeId, {
      commit_hash: clusterId,
      annotation_type: annotation.type,
      text: annotation.text,
      applies_to_fields: annotation.fields,
    });
    setAnnotating(null);
    onAnnotationSaved?.();
  };

  const qualityPercent = Math.round(
    (clusters.filter((c) => c.has_annotation || c.has_rich_pr).length / clusters.length) * 100
  );

  return (
    <div className="flex flex-col gap-2">
      {/* Quality bar */}
      <div className="flex items-center gap-3 px-1 mb-2">
        <span className="text-xs text-slate-500">Context quality</span>
        <div className="flex-1 h-2 rounded-full bg-slate-200">
          <div
            className="h-2 rounded-full bg-emerald-500 transition-all"
            style={{ width: `${qualityPercent}%` }}
          />
        </div>
        <span className="text-xs font-medium text-slate-700">{qualityPercent}%</span>
      </div>

      {/* Commit clusters */}
      {clusters.map((cluster) => (
        <div
          key={cluster.cluster_id}
          className="border border-slate-200 rounded-lg overflow-hidden"
        >
          {/* Cluster header */}
          <div
            className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 cursor-pointer"
            onClick={() => toggle(cluster.cluster_id)}
          >
            {/* Status icon */}
            <div className="flex-shrink-0">
              {cluster.has_annotation ? (
                <CheckCircle size={16} className="text-emerald-500" />
              ) : cluster.has_gap ? (
                <AlertTriangle size={16} className="text-amber-400" />
              ) : (
                <GitCommit size={16} className="text-slate-400" />
              )}
            </div>

            {/* Date + repo badges */}
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className="text-xs text-slate-500 flex-shrink-0">
                {formatDistanceToNow(new Date(cluster.approximate_date), { addSuffix: true })}
              </span>
              {cluster.commits.map((c) => (
                <span
                  key={c.commit_hash}
                  className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                    c.repo === 'backend'
                      ? 'bg-blue-100 text-blue-700'
                      : c.repo === 'frontend'
                      ? 'bg-purple-100 text-purple-700'
                      : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {c.repo}
                </span>
              ))}
              <span className="text-sm text-slate-800 truncate">
                {cluster.commits[0]?.message}
              </span>
            </div>

            {/* Expand toggle */}
            <div className="flex-shrink-0 text-slate-400">
              {expanded[cluster.cluster_id] ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
            </div>
          </div>

          {/* Expanded: diff + annotation */}
          {expanded[cluster.cluster_id] && (
            <div className="border-t border-slate-100 bg-slate-50">
              {/* PR context if available */}
              {cluster.commits[0]?.pr_title && (
                <div className="px-4 py-3 bg-blue-50 border-b border-slate-100">
                  <div className="text-xs font-medium text-blue-800 mb-1">
                    PR: {cluster.commits[0].pr_title}
                  </div>
                  {cluster.commits[0]?.pr_body && (
                    <p className="text-xs text-blue-700 line-clamp-3">
                      {cluster.commits[0].pr_body}
                    </p>
                  )}
                </div>
              )}

              {/* Diffs */}
              {cluster.commits.map((commit) => (
                <div key={commit.commit_hash} className="px-4 py-2">
                  <div className="text-xs text-slate-500 mb-1 font-mono">
                    {commit.commit_hash.slice(0, 7)} · {commit.file_path}
                  </div>
                  <pre className="text-xs bg-white border border-slate-200 rounded p-2 overflow-x-auto max-h-48">
                    <DiffHighlight diff={commit.diff} />
                  </pre>
                </div>
              ))}

              {/* Existing annotation */}
              {cluster.has_annotation && (
                <div className="mx-4 mb-3 p-3 bg-emerald-50 border border-emerald-200 rounded text-xs text-emerald-800">
                  <div className="font-medium mb-1">Your annotation</div>
                  {cluster.annotation.text}
                </div>
              )}

              {/* Add / edit annotation */}
              {annotating === cluster.cluster_id ? (
                <div className="px-4 pb-4">
                  <AnnotationEditor
                    commitMessage={cluster.commits[0]?.message}
                    onSave={(a) => handleSaveAnnotation(cluster.cluster_id, a)}
                    onCancel={() => setAnnotating(null)}
                  />
                </div>
              ) : (
                <div className="px-4 pb-3">
                  <button
                    onClick={() => setAnnotating(cluster.cluster_id)}
                    className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800 transition-colors"
                  >
                    <PenLine size={12} />
                    {cluster.has_annotation ? 'Edit annotation' : 'Add annotation'}
                  </button>
                </div>
              )}

              {/* Gap warning */}
              {cluster.has_gap && !cluster.has_annotation && (
                <div className="mx-4 mb-3 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
                  <span className="font-medium">LLM gap: </span>
                  {cluster.gap_description}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Minimal diff syntax highlighting (added = green, removed = red)
function DiffHighlight({ diff }) {
  if (!diff) return null;
  return diff.split('\n').map((line, i) => (
    <span
      key={i}
      className={
        line.startsWith('+')
          ? 'text-emerald-700'
          : line.startsWith('-')
          ? 'text-red-600'
          : line.startsWith('@@')
          ? 'text-blue-600'
          : 'text-slate-600'
      }
    >
      {line}{'\n'}
    </span>
  ));
}
