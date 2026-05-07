import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import { success, absent, type ToolResult } from "../contract.js";

export const GetTableForEntityInputSchema = z.object({
  scope:      z.string().min(1),
  entityName: z.string().min(1),
});
export type GetTableForEntityInput = z.infer<typeof GetTableForEntityInputSchema>;

export const FindColumnsWithPatternInputSchema = z.object({
  scope:   z.string().min(1),
  pattern: z.string().min(1),
  limit:   z.number().int().min(1).max(200).default(50),
});
export type FindColumnsWithPatternInput = z.infer<typeof FindColumnsWithPatternInputSchema>;

export const GetForeignKeysInputSchema = z.object({
  scope:     z.string().min(1),
  tableName: z.string().min(1),
});
export type GetForeignKeysInput = z.infer<typeof GetForeignKeysInputSchema>;

export interface TableRecord {
  id:        string;
  tableName: string;
  columns:   ColumnRecord[];
  orm?:      string;
  confidence: number;
}

export interface ColumnRecord {
  id:           string;
  columnName:   string;
  columnType:   string;
  nullable:     boolean;
  isPrimaryKey: boolean;
  isUnique:     boolean;
}

export interface ColumnSearchRecord {
  tableName:  string;
  columnName: string;
  columnType: string;
  tableId:    string;
  columnId:   string;
}

export interface ForeignKeyRecord {
  fromColumn:       string;
  referencedTable:  string;
  referencedColumn: string;
  constraintName?:  string;
}

export async function getTableForEntity(
  input: GetTableForEntityInput,
  graph: GraphClient,
): Promise<ToolResult<TableRecord>> {
  const { scope, entityName } = input;

  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (t { scope: $scope, type: "DatabaseTable" })
     WHERE toLower(t.tableName) = toLower($entityName) OR toLower(t.label) = toLower($entityName)
        OR toLower(coalesce(t.modelName, '')) = toLower($entityName)
        OR toLower(coalesce(t.entityClass, '')) = toLower($entityName)
     WITH t LIMIT 1
     OPTIONAL MATCH (t)-[:has_column]->(c { type: "DatabaseColumn" })
     RETURN t.id AS tableId, t.tableName AS tableName, t.confidence AS tableConfidence,
            coalesce(t.orm, 'unknown') AS orm,
            collect({
              id: c.id, columnName: c.columnName, columnType: c.columnType,
              nullable: c.nullable, isPrimaryKey: c.isPrimaryKey, isUnique: c.isUnique
            }) AS cols`,
    { scope, entityName },
  );

  if (rows.length === 0 || !rows[0]!["tableId"]) return absent("no_match", `No table for entity '${entityName}'`);

  const r = rows[0]!;
  const cols = (r["cols"] as Array<Record<string, unknown>>).filter(c => c["id"]);
  return success({
    id:        r["tableId"] as string,
    tableName: r["tableName"] as string,
    orm:       r["orm"] as string,
    columns:   cols.map(c => ({
      id:           c["id"] as string,
      columnName:   c["columnName"] as string,
      columnType:   c["columnType"] as string ?? "unknown",
      nullable:     c["nullable"] as boolean ?? true,
      isPrimaryKey: c["isPrimaryKey"] as boolean ?? false,
      isUnique:     c["isUnique"] as boolean ?? false,
    })),
    confidence: r["tableConfidence"] as number ?? 0.85,
  }, r["tableConfidence"] as number ?? 0.85);
}

export async function findColumnsWithPattern(
  input: FindColumnsWithPatternInput,
  graph: GraphClient,
): Promise<ToolResult<ColumnSearchRecord[]>> {
  const { scope, pattern, limit } = input;

  // Convert glob pattern to regex
  const regexStr = "(?i)" + pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");

  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (t { scope: $scope, type: "DatabaseTable" })-[:has_column]->(c { type: "DatabaseColumn" })
     WHERE c.columnName =~ $pattern
     RETURN t.tableName AS tableName, c.columnName AS columnName, c.columnType AS columnType,
            t.id AS tableId, c.id AS columnId
     LIMIT $limit`,
    { scope, pattern: regexStr, limit },
  );

  if (rows.length === 0) return absent("no_match", `No columns matching '${pattern}'`);

  return success(rows.map((r: Record<string, unknown>) => ({
    tableName:  r["tableName"] as string,
    columnName: r["columnName"] as string,
    columnType: r["columnType"] as string ?? "unknown",
    tableId:    r["tableId"] as string,
    columnId:   r["columnId"] as string,
  })), 0.90);
}

export async function getForeignKeys(
  input: GetForeignKeysInput,
  graph: GraphClient,
): Promise<ToolResult<ForeignKeyRecord[]>> {
  const { scope, tableName } = input;

  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (t { scope: $scope, type: "DatabaseTable" })-[r:references]->(target { type: "DatabaseTable" })
     WHERE toLower(t.tableName) = toLower($tableName)
     RETURN coalesce(r.fromColumn, '') AS fromColumn,
            target.tableName AS referencedTable,
            coalesce(r.toColumn, r.referencedColumn, 'id') AS referencedColumn,
            r.constraintName AS constraintName`,
    { scope, tableName },
  );

  if (rows.length === 0) return absent("no_match", `No foreign keys for table '${tableName}'`);

  return success(rows.map((r: Record<string, unknown>) => ({
    fromColumn:      r["fromColumn"] as string,
    referencedTable: r["referencedTable"] as string,
    referencedColumn: r["referencedColumn"] as string,
    constraintName:  r["constraintName"] as string | undefined,
  })), 0.90);
}
