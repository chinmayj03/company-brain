/**
 * tRPC-compatible HTTP client for the Company Brain backend.
 *
 * This is a plain-fetch implementation — it does NOT depend on @trpc/client.
 * Each procedure is called via POST /{procedureName} with body { input: {...} }.
 * The server returns { result: { data: <payload> } } on success.
 *
 * Absence semantics (ADR-0004): when result.result.data.absent === true,
 * the function returns null instead of throwing.
 *
 * Auth: Bearer JWT is read from localStorage key "cb_token" and attached to
 * every request, matching the same convention used by the axios client.
 */

const TRPC_BASE_URL =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_TRPC_API_BASE_URL) ||
  "http://localhost:8090/trpc";

// ── Core fetch helper ─────────────────────────────────────────────────────────

/**
 * POST to a tRPC procedure endpoint.
 *
 * @param {string} procedure - tRPC procedure name, e.g. "findSymbol"
 * @param {object} input     - Procedure input object
 * @returns {Promise<unknown | null>} Unwrapped data, or null for absences
 */
async function callProcedure(procedure, input) {
  const url = `${TRPC_BASE_URL}/${procedure}`;

  const headers = {
    "Content-Type": "application/json",
  };

  const token = localStorage.getItem("cb_token");
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    method:  "POST",
    headers,
    body:    JSON.stringify({ input }),
  });

  if (response.status === 401) {
    localStorage.removeItem("cb_token");
    window.location.href = "/login";
    return null;
  }

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    throw new Error(`tRPC ${procedure} failed (${response.status}): ${text}`);
  }

  const json = await response.json();

  // tRPC wire format: { result: { data: <payload> } }
  const data = json?.result?.data;

  // Absence envelope (ADR-0004): { absent: true, reason: string }
  if (data && typeof data === "object" && data.absent === true) {
    return null;
  }

  return data ?? null;
}

// ── Code symbol procedures ────────────────────────────────────────────────────

/**
 * Find a symbol by qualified name within a scope.
 *
 * @param {{ scope: string, qualifiedName: string }} input
 * @returns {Promise<import("../types").SymbolResult | null>}
 */
export const findSymbol = (input) =>
  callProcedure("findSymbol", input);

/**
 * Find all callers of a symbol by URN.
 *
 * @param {{ urn: string, depth?: number }} input
 * @returns {Promise<import("../types").CallersResult | null>}
 */
export const findCallers = (input) =>
  callProcedure("findCallers", input);

/**
 * Find all callees (symbols called by) a symbol URN.
 *
 * @param {{ urn: string, depth?: number }} input
 * @returns {Promise<import("../types").CalleesResult | null>}
 */
export const findCallees = (input) =>
  callProcedure("findCallees", input);

/**
 * Get the full function signature for a symbol URN.
 *
 * @param {{ urn: string }} input
 * @returns {Promise<import("../types").SignatureResult | null>}
 */
export const getFunctionSignature = (input) =>
  callProcedure("getFunctionSignature", input);

// ── Contract / API procedures ─────────────────────────────────────────────────

/**
 * Get the OpenAPI contract for a specific endpoint.
 *
 * @param {{ path: string, method: string, scope?: string }} input
 * @returns {Promise<import("../types").ContractResult | null>}
 */
export const getContractForEndpoint = (input) =>
  callProcedure("getContractForEndpoint", input);

/**
 * List all endpoints that implement a given contract operation.
 *
 * @param {{ operationId: string, scope?: string }} input
 * @returns {Promise<import("../types").ImplementationsResult | null>}
 */
export const listEndpointsImplementingContract = (input) =>
  callProcedure("listEndpointsImplementingContract", input);

/**
 * Get active drift signals, optionally filtered by severity.
 *
 * @param {{ scope?: string, severity?: "breaking" | "warning" | "info" }} input
 * @returns {Promise<import("../types").DriftSignalsResult | null>}
 */
export const getDriftSignals = (input) =>
  callProcedure("getDriftSignals", input);

// ── Schema / database procedures ──────────────────────────────────────────────

/**
 * Get the database table / Prisma model for an entity name.
 *
 * @param {{ entityName: string, scope?: string }} input
 * @returns {Promise<import("../types").TableResult | null>}
 */
export const getTableForEntity = (input) =>
  callProcedure("getTableForEntity", input);

/**
 * Find all columns whose name matches a glob pattern (e.g. "*_id", "email*").
 *
 * @param {{ pattern: string, scope?: string }} input
 * @returns {Promise<import("../types").ColumnsResult | null>}
 */
export const findColumnsWithPattern = (input) =>
  callProcedure("findColumnsWithPattern", input);

/**
 * Get all foreign key relationships for a table.
 *
 * @param {{ tableName: string, scope?: string }} input
 * @returns {Promise<import("../types").ForeignKeysResult | null>}
 */
export const getForeignKeys = (input) =>
  callProcedure("getForeignKeys", input);

// ── Repository / navigation procedures ───────────────────────────────────────

/**
 * Get the repo map — top-level file tree with annotations.
 *
 * @param {{ scope: string, maxDepth?: number }} input
 * @returns {Promise<import("../types").RepoMapResult | null>}
 */
export const getRepoMap = (input) =>
  callProcedure("getRepoMap", input);
