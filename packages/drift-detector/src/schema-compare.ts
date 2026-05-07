/**
 * Schema comparison logic for drift detection.
 *
 * Compares an inferred response shape (from CoreTs return type string)
 * against a declared contract schema (from ContractResponseSchema.schema_json).
 *
 * v1 algorithm: name-based structural comparison.
 * Extracts top-level required fields from both sides and diffs them.
 */

import type { DriftFinding, DriftSeverity, DeclaredField } from "./types.js";

/**
 * Parse top-level field names from a JSON Schema object string.
 * Returns an array of { name, type, required } for top-level properties.
 */
export function parseSchemaFields(schemaJson: string | null | undefined): DeclaredField[] {
  if (!schemaJson) return [];
  try {
    const schema = JSON.parse(schemaJson) as Record<string, unknown>;
    return extractTopLevelFields(schema);
  } catch {
    return [];
  }
}

function extractTopLevelFields(schema: Record<string, unknown>): DeclaredField[] {
  // Handle allOf: take fields from first branch only (v1 limitation)
  if (Array.isArray(schema["allOf"])) {
    const first = (schema["allOf"] as unknown[])[0];
    if (first && typeof first === "object") {
      return extractTopLevelFields(first as Record<string, unknown>);
    }
  }

  const properties = schema["properties"];
  if (!properties || typeof properties !== "object") return [];

  const requiredSet = new Set<string>(
    Array.isArray(schema["required"]) ? (schema["required"] as string[]) : [],
  );

  return Object.entries(properties as Record<string, unknown>).map(([name, propSchema]) => {
    const type =
      typeof propSchema === "object" && propSchema !== null
        ? String((propSchema as Record<string, unknown>)["type"] ?? "unknown")
        : "unknown";
    return { name, type, required: requiredSet.has(name) };
  });
}

/**
 * Parse top-level field names from a TypeScript return type string.
 * e.g. "{ id: string; name: string; amount: number }" → [{name:"id",...}, ...]
 *
 * This is a best-effort heuristic parser for inline object types.
 */
export function parseReturnTypeFields(returnType: string | null | undefined): DeclaredField[] {
  if (!returnType) return [];

  // Strip Promise<...> wrapper
  const inner = returnType.replace(/^Promise<(.+)>$/, "$1").trim();

  // Only handle inline object types: { field: type; ... }
  if (!inner.startsWith("{") || !inner.endsWith("}")) return [];

  const body = inner.slice(1, -1);
  const fields: DeclaredField[] = [];

  // Simple parser: split on ; and parse each "name: type" pair
  for (const entry of body.split(";")) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const colonIdx = trimmed.indexOf(":");
    if (colonIdx < 0) continue;
    const name = trimmed.slice(0, colonIdx).trim().replace(/\?$/, "");
    const type = trimmed.slice(colonIdx + 1).trim();
    const required = !trimmed.slice(0, colonIdx).endsWith("?");
    if (name) fields.push({ name, type, required });
  }

  return fields;
}

/**
 * Compare implementation fields (inferred) against contract fields (declared).
 * Returns an array of drift findings (empty = no drift).
 */
export function compareFields(
  implementedFields: DeclaredField[],
  contractFields: DeclaredField[],
): DriftFinding[] {
  if (contractFields.length === 0) return []; // no contract to compare against

  const findings: DriftFinding[] = [];

  const implMap = new Map(implementedFields.map((f) => [f.name, f]));
  const contractMap = new Map(contractFields.map((f) => [f.name, f]));

  // Check contract required fields against implementation
  for (const cf of contractFields) {
    if (!cf.required) continue;
    const impl = implMap.get(cf.name);
    if (!impl) {
      findings.push({
        severity: "breaking",
        description: `Required field "${cf.name}" (${cf.type}) is declared in contract but missing from implementation`,
        fields: [cf.name],
      });
      continue;
    }

    // Type check: simple string comparison only (v1)
    if (impl.type && cf.type && cf.type !== "unknown" && impl.type !== "unknown") {
      if (!typesCompatible(impl.type, cf.type)) {
        findings.push({
          severity: "breaking",
          description: `Field "${cf.name}" has incompatible type: implementation has "${impl.type}", contract declares "${cf.type}"`,
          fields: [cf.name],
        });
      }
    }
  }

  // Check for optional contract fields missing from implementation
  for (const cf of contractFields) {
    if (cf.required) continue; // already handled above
    if (!implMap.has(cf.name)) {
      findings.push({
        severity: "warning",
        description: `Optional field "${cf.name}" declared in contract but not found in implementation`,
        fields: [cf.name],
      });
    }
  }

  // Extra fields in implementation not in contract
  for (const [name] of implMap) {
    if (!contractMap.has(name)) {
      findings.push({
        severity: "info",
        description: `Field "${name}" is present in implementation but not declared in contract`,
        fields: [name],
      });
    }
  }

  return findings;
}

/**
 * Coarse type compatibility check between TypeScript and JSON Schema types.
 */
function typesCompatible(implType: string, contractType: string): boolean {
  const normalize = (t: string): string => {
    const lower = t.toLowerCase().trim();
    if (lower === "number" || lower === "integer" || lower === "float") return "number";
    if (lower === "string") return "string";
    if (lower === "boolean") return "boolean";
    if (lower.includes("[]") || lower === "array") return "array";
    return lower;
  };
  return normalize(implType) === normalize(contractType);
}
