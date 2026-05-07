import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import { success, absent, type ToolResult } from "../contract.js";

export const GetFileSummaryInputSchema = z.object({
  scope:    z.string().min(1),
  filePath: z.string().min(1),
});
export type GetFileSummaryInput = z.infer<typeof GetFileSummaryInputSchema>;

export const ListFilesInInputSchema = z.object({
  scope:     z.string().min(1),
  directory: z.string().min(1),
  recursive: z.boolean().default(false),
});
export type ListFilesInInput = z.infer<typeof ListFilesInInputSchema>;

export interface FileSummary {
  id:        string;
  filePath:  string;
  exports:   SymbolRef[];
  imports:   string[];
  language:  string;
  confidence: number;
}

export interface FileRecord {
  id:        string;
  filePath:  string;
  type:      string;
  language?: string;
}

interface SymbolRef { id: string; label: string; type: string; }

export async function getFileSummary(
  input: GetFileSummaryInput,
  graph: GraphClient,
): Promise<ToolResult<FileSummary>> {
  const { scope, filePath } = input;

  // Find the file node
  const fileRows = await graph.runRead<Record<string, unknown>>(
    `MATCH (f { scope: $scope })
     WHERE (f.path = $filePath OR f.filePath = $filePath) AND f.type IN ["File","Module"]
     RETURN f.id AS id, f.path AS path, coalesce(f.lang, 'unknown') AS lang, f.confidence AS confidence
     LIMIT 1`,
    { scope, filePath },
  );
  if (fileRows.length === 0) return absent("no_match", `File not found: ${filePath}`);

  const fileRow = fileRows[0]!;
  const fileId = fileRow["id"] as string;

  // Exports (symbols defined in this file)
  const exportRows = await graph.runRead<Record<string, unknown>>(
    `MATCH (f { id: $fileId })-[:defines]->(s)
     RETURN s.id AS id, s.label AS label, s.type AS type
     LIMIT 100`,
    { fileId },
  );

  // Imports
  const importRows = await graph.runRead<Record<string, unknown>>(
    `MATCH (f { id: $fileId })-[r:imports]->(target)
     RETURN coalesce(r.specifier, target.id) AS specifier
     LIMIT 50`,
    { fileId },
  );

  return success({
    id:       fileId,
    filePath: (fileRow["path"] as string) ?? filePath,
    exports:  exportRows.map((r: Record<string, unknown>) => ({ id: r["id"] as string, label: r["label"] as string, type: r["type"] as string })),
    imports:  importRows.map((r: Record<string, unknown>) => r["specifier"] as string),
    language: fileRow["lang"] as string,
    confidence: fileRow["confidence"] as number ?? 0.95,
  }, fileRow["confidence"] as number ?? 0.95);
}

export async function listFilesIn(
  input: ListFilesInInput,
  graph: GraphClient,
): Promise<ToolResult<FileRecord[]>> {
  const { scope, directory, recursive } = input;
  const dirPrefix = directory.replace(/\/$/, "") + "/";

  const pathCondition = recursive
    ? "f.path STARTS WITH $dirPrefix"
    : "(f.path STARTS WITH $dirPrefix AND NOT f.path CONTAINS '/' + $dirPrefix)";

  // Simpler: just match the prefix
  const cypher = `
    MATCH (f { scope: $scope })
    WHERE (f.type = "File" OR f.type = "Module")
      AND (f.path STARTS WITH $dirPrefix OR f.path STARTS WITH $directory)
    RETURN f.id AS id, coalesce(f.path, f.filePath) AS filePath, f.type AS type, f.lang AS language
    LIMIT 200
  `;

  const rows = await graph.runRead<Record<string, unknown>>(cypher, { scope, directory, dirPrefix });
  if (rows.length === 0) return absent("no_match", `No files found in ${directory}`);

  const results: FileRecord[] = rows.map((r: Record<string, unknown>) => ({
    id:       r["id"] as string,
    filePath: r["filePath"] as string,
    type:     r["type"] as string,
    language: r["language"] as string | undefined,
  }));
  return success(results, 0.90);
}
