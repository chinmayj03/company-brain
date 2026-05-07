import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

// Java type → SQL type mapping
const JAVA_TYPE_MAP: Record<string, string> = {
  "String": "VARCHAR", "string": "VARCHAR",
  "Long": "BIGINT", "long": "BIGINT",
  "Integer": "INTEGER", "int": "INTEGER",
  "Short": "SMALLINT", "short": "SMALLINT",
  "Boolean": "BOOLEAN", "boolean": "BOOLEAN",
  "Double": "DOUBLE PRECISION", "double": "DOUBLE PRECISION",
  "Float": "REAL", "float": "REAL",
  "BigDecimal": "DECIMAL", "LocalDate": "DATE",
  "LocalDateTime": "TIMESTAMP", "ZonedDateTime": "TIMESTAMPTZ",
  "UUID": "UUID", "byte[]": "BYTEA", "Byte[]": "BYTEA",
};

function camelToSnake(name: string): string {
  return name.replace(/([A-Z])/g, (_, c, i) => i > 0 ? `_${c.toLowerCase()}` : c.toLowerCase());
}

interface JpaEntity {
  className: string;
  tableName: string;
  columns: Array<{ fieldName: string; columnName: string; javaType: string; sqlType: string; nullable: boolean; isPk: boolean; isUnique: boolean; }>;
  foreignKeys: Array<{ fieldName: string; columnName: string; referencedEntity: string; }>;
}

function parseJpaFile(content: string): JpaEntity | null {
  // Must be a @Entity class
  if (!/@Entity\b/.test(content)) return null;
  if (!/@Table\b/.test(content) && !/@Entity\b/.test(content)) return null;

  // Extract class name
  const classMatch = /(?:public\s+)?class\s+(\w+)/.exec(content);
  if (!classMatch) return null;
  const className = classMatch[1]!;

  // Extract table name
  const tableAnnotation = /@Table\s*\([^)]*name\s*=\s*"([^"]+)"/.exec(content);
  const tableName = tableAnnotation ? tableAnnotation[1]! : camelToSnake(className);

  const entity: JpaEntity = { className, tableName, columns: [], foreignKeys: [] };

  // Parse field annotations + field declarations
  // Strategy: look for @Column / @Id / @ManyToOne / @JoinColumn blocks followed by field declaration
  const fieldPattern = /(@(?:Id|GeneratedValue|Column|ManyToOne|JoinColumn|OneToOne|Enumerated|Temporal|Lob)[^;]*?)\s+(private|protected|public|)\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*;/gs;
  let m: RegExpExecArray | null;

  while ((m = fieldPattern.exec(content)) !== null) {
    const annotations = m[1] ?? "";
    const javaType = m[3]?.replace(/<.*>/, "") ?? "Object";
    const fieldName = m[4] ?? "";

    const isId = annotations.includes("@Id");
    const isManyToOne = annotations.includes("@ManyToOne") || annotations.includes("@OneToOne");
    const isTransient = annotations.includes("@Transient");
    if (isTransient) continue;

    // Extract @Column attributes
    const colAnnotation = /@Column\s*\(([^)]*)\)/.exec(annotations);
    const colNameMatch = colAnnotation ? /name\s*=\s*"([^"]+)"/.exec(colAnnotation[1] ?? "") : null;
    const notNullMatch = colAnnotation ? /nullable\s*=\s*false/.exec(colAnnotation[1] ?? "") : null;
    const uniqueMatch = colAnnotation ? /unique\s*=\s*true/.exec(colAnnotation[1] ?? "") : null;

    if (isManyToOne) {
      // @JoinColumn gives us the FK column name
      const joinColMatch = /@JoinColumn\s*\([^)]*name\s*=\s*"([^"]+)"/.exec(annotations);
      const fkColName = joinColMatch ? joinColMatch[1]! : camelToSnake(fieldName) + "_id";
      entity.foreignKeys.push({
        fieldName, columnName: fkColName, referencedEntity: javaType,
      });
      // Also add as a column
      entity.columns.push({
        fieldName, columnName: fkColName, javaType: "Long",
        sqlType: "BIGINT", nullable: true, isPk: false, isUnique: false,
      });
    } else {
      const columnName = colNameMatch ? colNameMatch[1]! : camelToSnake(fieldName);
      const sqlType = JAVA_TYPE_MAP[javaType] ?? "VARCHAR";
      entity.columns.push({
        fieldName, columnName, javaType, sqlType,
        nullable: !isId && !notNullMatch,
        isPk: isId, isUnique: !!uniqueMatch,
      });
    }
  }

  return entity;
}

function findJavaFiles(repoRoot: string): string[] {
  const results: string[] = [];
  const skip = new Set(["node_modules", ".git", "target", "build", ".gradle"]);
  function walk(dir: string) {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory() && !skip.has(entry.name)) walk(path.join(dir, entry.name));
      else if (entry.isFile() && (entry.name.endsWith(".java") || entry.name.endsWith(".kt"))) {
        results.push(path.join(dir, entry.name));
      }
    }
  }
  walk(repoRoot);
  return results;
}

export class FrameworkJpaExtractor implements ExtractorPlugin {
  readonly name = "framework-jpa";
  readonly languages = ["java", "kotlin"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;
    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    const javaFiles = findJavaFiles(repoRoot);
    if (javaFiles.length === 0) {
      log.info("[framework-jpa] no Java/Kotlin files found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const schemaId = buildUrn("schema", scope, "jpa-schema");
    let entityCount = 0;

    for (const absPath of javaFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const entity = parseJpaFile(content);
      if (!entity) continue;
      entityCount++;

      const checksum = sha256short(content);

      if (entityCount === 1) {
        nodes.push({
          id: schemaId, label: "JPA Schema", type: "DatabaseSchema", scope,
          source: "schema", source_checksum: sha256short(scope),
          confidence: 0.85, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { orm: "jpa" },
        });
      }

      const tableId = buildUrn("schema", scope, "jpa-schema", entity.tableName);
      nodes.push({
        id: tableId, label: entity.tableName, type: "DatabaseTable", scope,
        source: "schema", source_checksum: checksum,
        confidence: 0.85, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { tableName: entity.tableName, entityClass: entity.className, orm: "jpa", sourceFile: relPath },
      });
      edges.push({ fromId: schemaId, toId: tableId, type: "has_table", source: "schema", confidence: 0.85 });

      for (const col of entity.columns) {
        const colId = buildUrn("schema", scope, "jpa-schema", entity.tableName, col.columnName);
        nodes.push({
          id: colId, label: col.columnName, type: "DatabaseColumn", scope,
          source: "schema", source_checksum: checksum,
          confidence: 0.85, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { columnName: col.columnName, columnType: col.sqlType, nullable: col.nullable, isPrimaryKey: col.isPk, isUnique: col.isUnique, javaType: col.javaType },
        });
        edges.push({ fromId: tableId, toId: colId, type: "has_column", source: "schema", confidence: 0.85 });
      }

      for (const fk of entity.foreignKeys) {
        const fkTargetId = buildUrn("schema", scope, "jpa-schema", fk.referencedEntity.replace(/([A-Z])/g, (_, c, i) => i > 0 ? `_${c.toLowerCase()}` : c.toLowerCase()));
        edges.push({
          fromId: tableId, toId: fkTargetId, type: "references",
          source: "schema", confidence: 0.85,
          properties: { fromColumn: fk.columnName, referencedEntity: fk.referencedEntity },
        });
      }
    }

    if (entityCount === 0) {
      log.info("[framework-jpa] no @Entity classes found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-jpa] wrote ${nodesWritten} nodes, ${edgesWritten} edges (${entityCount} entities)`);
    return { nodesWritten, edgesWritten };
  }
}
