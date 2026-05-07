import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";
import { parseSqlFile } from "./parser.js";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

// Common migration directory patterns
const MIGRATION_DIRS = [
  "src/main/resources/db/migration",   // Flyway (Spring Boot)
  "db/migrate",                         // Rails
  "migrations",                         // generic
  "database/migrations",               // Laravel
  "alembic/versions",                  // Alembic
  "prisma/migrations",                 // Prisma
  "db/migrations",
  "sql/migrations",
];

function findSqlFiles(repoRoot: string): string[] {
  const found: string[] = [];
  const skip = new Set(["node_modules", ".git", "dist", "build", "target"]);

  // Search migration directories
  for (const migDir of MIGRATION_DIRS) {
    const abs = path.join(repoRoot, migDir);
    if (!fs.existsSync(abs)) continue;
    for (const entry of fs.readdirSync(abs, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.endsWith(".sql")) {
        found.push(path.join(abs, entry.name));
      }
    }
  }

  // Also recursive search for *.sql files (limit depth to avoid huge scans)
  function walk(dir: string, depth: number) {
    if (depth > 5 || !fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory() && !skip.has(entry.name)) walk(path.join(dir, entry.name), depth + 1);
      else if (entry.isFile() && entry.name.endsWith(".sql")) {
        const abs = path.join(dir, entry.name);
        if (!found.includes(abs)) found.push(abs);
      }
    }
  }
  walk(repoRoot, 0);

  return [...new Set(found)];
}

export class FrameworkSqlExtractor implements ExtractorPlugin {
  readonly name = "framework-sql";
  readonly languages = ["sql"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;
    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    const sqlFiles = findSqlFiles(repoRoot);
    if (sqlFiles.length === 0) {
      log.info("[framework-sql] no SQL migration files found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    log.info(`[framework-sql] scanning ${sqlFiles.length} SQL files`);

    // Accumulate tables across files (later migrations override earlier)
    const tableRegistry = new Map<string, { table: ReturnType<typeof parseSqlFile>[number]; checksum: string }>();

    for (const absPath of sqlFiles.sort()) {  // sort for Flyway ordering V1__ V2__ ...
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const checksum = sha256short(content);
      const tables = parseSqlFile(content, relPath);
      for (const table of tables) {
        tableRegistry.set(table.tableName, { table, checksum });
      }
    }

    // Schema container
    const schemaId = buildUrn("schema", scope, "sql-schema");
    nodes.push({
      id: schemaId, label: "SQL Schema", type: "DatabaseSchema", scope,
      source: "schema", source_checksum: sha256short(scope),
      confidence: 0.90, valid_from_commit: commitSha, valid_to_commit: null,
      properties: { orm: "sql-ddl", fileCount: sqlFiles.length },
    });

    for (const { table, checksum } of tableRegistry.values()) {
      const tableId = buildUrn("schema", scope, "sql-schema", table.tableName);
      nodes.push({
        id: tableId, label: table.tableName, type: "DatabaseTable", scope,
        source: "schema", source_checksum: checksum,
        confidence: 0.90, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { tableName: table.tableName, orm: "sql-ddl", sourceFile: table.sourceFile },
      });
      edges.push({ fromId: schemaId, toId: tableId, type: "has_table", source: "schema", confidence: 0.90 });

      for (const col of table.columns) {
        const colId = buildUrn("schema", scope, "sql-schema", table.tableName, col.name);
        nodes.push({
          id: colId, label: col.name, type: "DatabaseColumn", scope,
          source: "schema", source_checksum: checksum,
          confidence: 0.90, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { columnName: col.name, columnType: col.type, nullable: col.nullable, isPrimaryKey: col.isPrimaryKey, isUnique: col.isUnique, defaultValue: col.defaultValue ?? null },
        });
        edges.push({ fromId: tableId, toId: colId, type: "has_column", source: "schema", confidence: 0.90 });
      }

      for (const fk of table.foreignKeys) {
        const fkTargetId = buildUrn("schema", scope, "sql-schema", fk.referencedTable);
        edges.push({
          fromId: tableId, toId: fkTargetId, type: "references",
          source: "schema", confidence: 0.90,
          properties: { fromColumn: fk.column, toColumn: fk.referencedColumn, constraintName: fk.constraintName ?? null },
        });
      }
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-sql] wrote ${nodesWritten} nodes, ${edgesWritten} edges (${tableRegistry.size} tables)`);
    return { nodesWritten, edgesWritten };
  }
}
