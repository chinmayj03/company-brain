import { useState } from 'react';
import { getTableForEntity, findColumnsWithPattern, getForeignKeys } from '../api/trpc-client';

// ── Helpers ───────────────────────────────────────────────────────────────────

function TypeBadge({ type }) {
  const t = (type ?? '').toLowerCase();
  const color =
    t.includes('int') || t.includes('serial')           ? 'bg-blue-100 text-blue-700'
    : t.includes('varchar') || t.includes('text') || t.includes('string') ? 'bg-green-100 text-green-700'
    : t.includes('bool')                                ? 'bg-purple-100 text-purple-700'
    : t.includes('timestamp') || t.includes('date')     ? 'bg-yellow-100 text-yellow-700'
    : t.includes('float') || t.includes('decimal') || t.includes('numeric') ? 'bg-orange-100 text-orange-700'
    :                                                     'bg-slate-100 text-slate-600';

  return (
    <span className={`px-1.5 py-0.5 text-xs font-mono rounded ${color}`}>
      {type ?? 'unknown'}
    </span>
  );
}

// ── Section: Table detail ─────────────────────────────────────────────────────

function TableDetail({ table, foreignKeys, fkLoading, fkError }) {
  const columns = Array.isArray(table.columns) ? table.columns : [];

  return (
    <div className="space-y-5">
      {/* Table header */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 bg-brand-100 rounded-lg flex items-center justify-center">
          <span className="text-brand-700 text-sm font-bold">T</span>
        </div>
        <div>
          <h3 className="font-semibold text-slate-900">{table.tableName ?? table.name}</h3>
          {table.description && (
            <p className="text-xs text-slate-500 mt-0.5">{table.description}</p>
          )}
        </div>
      </div>

      {/* Columns */}
      <div>
        <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">
          Columns ({columns.length})
        </h4>
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Name</th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Type</th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Flags</th>
                <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Default</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((col, i) => (
                <tr
                  key={col.name ?? i}
                  className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/40'}`}
                >
                  <td className="px-3 py-2 font-mono text-xs text-slate-800 font-medium">
                    {col.name}
                  </td>
                  <td className="px-3 py-2">
                    <TypeBadge type={col.type} />
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {(col.isPrimaryKey || col.primary_key) && (
                        <span className="px-1.5 py-0.5 text-xs font-semibold rounded bg-amber-100 text-amber-800 border border-amber-200">
                          PK
                        </span>
                      )}
                      {(col.isForeignKey || col.foreign_key) && (
                        <span className="px-1.5 py-0.5 text-xs font-semibold rounded bg-cyan-100 text-cyan-800 border border-cyan-200">
                          FK
                        </span>
                      )}
                      {(col.isNullable ?? col.nullable) === false && (
                        <span className="px-1.5 py-0.5 text-xs rounded bg-red-50 text-red-700 border border-red-100">
                          NOT NULL
                        </span>
                      )}
                      {(col.isUnique || col.unique) && (
                        <span className="px-1.5 py-0.5 text-xs rounded bg-violet-50 text-violet-700 border border-violet-100">
                          UNIQUE
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400">
                    {col.defaultValue ?? col.default ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Foreign keys */}
      <div>
        <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">
          Foreign Key Relationships
        </h4>
        {fkLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="animate-spin">⟳</span> Loading relationships…
          </div>
        )}
        {fkError && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {fkError}
          </div>
        )}
        {!fkLoading && !fkError && Array.isArray(foreignKeys) && foreignKeys.length === 0 && (
          <p className="text-sm text-slate-400 italic">No foreign key relationships found.</p>
        )}
        {!fkLoading && Array.isArray(foreignKeys) && foreignKeys.length > 0 && (
          <div className="space-y-2">
            {foreignKeys.map((fk, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-3 py-2 bg-cyan-50 rounded-lg border border-cyan-100 text-sm"
              >
                <span className="font-mono text-xs text-cyan-700 font-medium">{fk.fromColumn ?? fk.column}</span>
                <span className="text-slate-400">→</span>
                <span className="font-mono text-xs text-slate-700">
                  {fk.toTable ?? fk.referenced_table}.{fk.toColumn ?? fk.referenced_column}
                </span>
                {fk.onDelete && (
                  <span className="ml-auto text-xs text-slate-400">ON DELETE {fk.onDelete}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Column search results ─────────────────────────────────────────────────────

function ColumnSearchResults({ results }) {
  if (!Array.isArray(results) || results.length === 0) {
    return <p className="text-sm text-slate-400 italic">No columns matched the pattern.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Table</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Column</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Type</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600">Flags</th>
          </tr>
        </thead>
        <tbody>
          {results.map((col, i) => (
            <tr
              key={i}
              className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/40'}`}
            >
              <td className="px-3 py-2 font-mono text-xs text-slate-500">{col.tableName ?? col.table}</td>
              <td className="px-3 py-2 font-mono text-xs text-slate-800 font-medium">{col.name}</td>
              <td className="px-3 py-2"><TypeBadge type={col.type} /></td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {(col.isPrimaryKey || col.primary_key) && (
                    <span className="px-1.5 py-0.5 text-xs font-semibold rounded bg-amber-100 text-amber-800">PK</span>
                  )}
                  {(col.isForeignKey || col.foreign_key) && (
                    <span className="px-1.5 py-0.5 text-xs font-semibold rounded bg-cyan-100 text-cyan-800">FK</span>
                  )}
                  {(col.isNullable ?? col.nullable) === false && (
                    <span className="px-1.5 py-0.5 text-xs rounded bg-red-50 text-red-700">NOT NULL</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SchemaExplorerPage() {
  // Table search
  const [tableQuery,   setTableQuery]   = useState('');
  const [table,        setTable]        = useState(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError,   setTableError]   = useState(null);

  // Foreign keys (loaded after table)
  const [foreignKeys, setForeignKeys]   = useState(null);
  const [fkLoading,   setFkLoading]     = useState(false);
  const [fkError,     setFkError]       = useState(null);

  // Column pattern search
  const [columnPattern,        setColumnPattern]        = useState('');
  const [columnResults,        setColumnResults]        = useState(null);
  const [columnSearchLoading,  setColumnSearchLoading]  = useState(false);
  const [columnSearchError,    setColumnSearchError]    = useState(null);

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleTableSearch(e) {
    e.preventDefault();
    if (!tableQuery.trim()) return;

    setTableLoading(true);
    setTableError(null);
    setTable(null);
    setForeignKeys(null);
    setFkError(null);

    try {
      const result = await getTableForEntity({ entityName: tableQuery.trim() });
      setTable(result);

      if (result) {
        setFkLoading(true);
        try {
          const fkResult = await getForeignKeys({ tableName: tableQuery.trim() });
          setForeignKeys(fkResult?.foreignKeys ?? fkResult ?? []);
        } catch (err) {
          setFkError(err.message ?? 'Failed to load foreign keys');
        } finally {
          setFkLoading(false);
        }
      }
    } catch (err) {
      setTableError(err.message ?? 'Failed to fetch table');
    } finally {
      setTableLoading(false);
    }
  }

  async function handleColumnSearch(e) {
    e.preventDefault();
    if (!columnPattern.trim()) return;

    setColumnSearchLoading(true);
    setColumnSearchError(null);
    setColumnResults(null);

    try {
      // Append * glob if user didn't include one, for convenience
      const pattern = columnPattern.trim();
      const result = await findColumnsWithPattern({ pattern });
      setColumnResults(result?.columns ?? result ?? []);
    } catch (err) {
      setColumnSearchError(err.message ?? 'Failed to search columns');
    } finally {
      setColumnSearchLoading(false);
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-8 max-w-5xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Schema Explorer</h1>
        <p className="text-sm text-slate-500 mt-1">
          Browse database tables, column definitions, and relationships.
        </p>
      </div>

      {/* ── Table search ── */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-800 mb-4">Table / Entity Lookup</h2>

        <form onSubmit={handleTableSearch} className="flex gap-3">
          <input
            type="text"
            value={tableQuery}
            onChange={e => setTableQuery(e.target.value)}
            placeholder="User, Order, payment_transactions…"
            className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          <button
            type="submit"
            disabled={tableLoading || !tableQuery.trim()}
            className="px-4 py-2 text-sm font-medium bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {tableLoading ? 'Searching…' : 'Lookup'}
          </button>
        </form>

        {tableError && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {tableError}
          </div>
        )}

        {tableLoading && (
          <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
            <span className="animate-spin">⟳</span> Querying schema graph…
          </div>
        )}

        {table && !tableLoading && (
          <div className="mt-5">
            <TableDetail
              table={table}
              foreignKeys={foreignKeys}
              fkLoading={fkLoading}
              fkError={fkError}
            />
          </div>
        )}

        {table === null && !tableLoading && tableQuery && !tableError && (
          <p className="mt-4 text-sm text-slate-400 italic">
            No table found for "{tableQuery}". Try the Prisma model name or exact table name.
          </p>
        )}
      </section>

      {/* ── Column pattern search ── */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-800 mb-1">Column Pattern Search</h2>
        <p className="text-xs text-slate-500 mb-4">
          Search for columns by name pattern across all tables. Supports glob wildcards (e.g. <code className="font-mono">*_id</code>, <code className="font-mono">email*</code>, <code className="font-mono">*amount*</code>).
        </p>

        <form onSubmit={handleColumnSearch} className="flex gap-3">
          <input
            type="text"
            value={columnPattern}
            onChange={e => setColumnPattern(e.target.value)}
            placeholder="*_id, email*, created_*"
            className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
          />
          <button
            type="submit"
            disabled={columnSearchLoading || !columnPattern.trim()}
            className="px-4 py-2 text-sm font-medium bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {columnSearchLoading ? 'Searching…' : 'Search'}
          </button>
        </form>

        {columnSearchError && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {columnSearchError}
          </div>
        )}

        {columnSearchLoading && (
          <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
            <span className="animate-spin">⟳</span> Searching columns…
          </div>
        )}

        {columnResults !== null && !columnSearchLoading && (
          <div className="mt-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-slate-500">
                {Array.isArray(columnResults) ? columnResults.length : 0} column(s) matched
              </span>
            </div>
            <ColumnSearchResults results={columnResults} />
          </div>
        )}
      </section>
    </div>
  );
}
