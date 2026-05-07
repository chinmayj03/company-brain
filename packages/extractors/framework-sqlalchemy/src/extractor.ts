import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

// Python type → SQL type
const PY_TYPE_MAP: Record<string, string> = {
  "String": "VARCHAR", "Text": "TEXT", "Integer": "INTEGER", "BigInteger": "BIGINT",
  "SmallInteger": "SMALLINT", "Float": "REAL", "Numeric": "DECIMAL", "Boolean": "BOOLEAN",
  "Date": "DATE", "DateTime": "TIMESTAMP", "Time": "TIME", "LargeBinary": "BYTEA",
  "JSON": "JSON", "JSONB": "JSONB", "UUID": "UUID", "Enum": "VARCHAR",
  // Django field types
  "CharField": "VARCHAR", "TextField": "TEXT", "IntegerField": "INTEGER",
  "BigIntegerField": "BIGINT", "SmallIntegerField": "SMALLINT",
  "FloatField": "REAL", "DecimalField": "DECIMAL", "BooleanField": "BOOLEAN",
  "DateField": "DATE", "DateTimeField": "TIMESTAMP", "TimeField": "TIME",
  "BinaryField": "BYTEA", "JSONField": "JSONB", "UUIDField": "UUID",
  "EmailField": "VARCHAR", "URLField": "VARCHAR", "SlugField": "VARCHAR",
  "IPAddressField": "VARCHAR", "GenericIPAddressField": "VARCHAR",
  "PositiveIntegerField": "INTEGER", "PositiveSmallIntegerField": "SMALLINT",
  "AutoField": "INTEGER", "BigAutoField": "BIGINT",
};

interface PyColumn { name: string; type: string; nullable: boolean; isPk: boolean; isUnique: boolean; }
interface PyFk { column: string; referencedTable: string; }
interface PyModel { className: string; tableName: string; columns: PyColumn[]; foreignKeys: PyFk[]; }

function parseSqlAlchemyFile(content: string): PyModel[] {
  const models: PyModel[] = [];

  // SQLAlchemy: class Foo(Base) with __tablename__
  const classPattern = /^class\s+(\w+)\s*\([^)]*(?:Base|Model|db\.Model)[^)]*\)\s*:/gm;
  let classMatch: RegExpExecArray | null;

  while ((classMatch = classPattern.exec(content)) !== null) {
    const className = classMatch[1]!;
    const classStart = classMatch.index + classMatch[0].length;
    // Extract class body (until next class or EOF)
    const nextClassMatch = /^class\s+\w+/gm.exec(content.slice(classStart));
    const classBody = nextClassMatch
      ? content.slice(classStart, classStart + nextClassMatch.index!)
      : content.slice(classStart);

    // __tablename__
    const tableNameMatch = /__tablename__\s*=\s*['"]([^'"]+)['"]/.exec(classBody);
    const tableName = tableNameMatch ? tableNameMatch[1]! : className.toLowerCase();

    const model: PyModel = { className, tableName, columns: [], foreignKeys: [] };

    // SQLAlchemy columns: field = Column(Type, ...) or field = mapped_column(Type, ...)
    const colPattern = /(\w+)\s*(?::\s*\w+[^=]*)?\s*=\s*(?:mapped_column|Column)\s*\(\s*([^)]+)\)/g;
    let colMatch: RegExpExecArray | null;
    while ((colMatch = colPattern.exec(classBody)) !== null) {
      const fieldName = colMatch[1]!;
      if (["__tablename__", "__table_args__"].includes(fieldName)) continue;
      const args = colMatch[2] ?? "";

      // Extract type (first positional arg or keyword type=)
      const typeMatch = /^(\w+)/.exec(args.trim()) ?? /type_=(\w+)/.exec(args);
      const pyType = typeMatch ? typeMatch[1]! : "String";
      const sqlType = PY_TYPE_MAP[pyType] ?? "VARCHAR";

      const isPk = args.includes("primary_key=True");
      const nullable = !isPk && !args.includes("nullable=False");
      const isUnique = args.includes("unique=True");

      // ForeignKey
      const fkMatch = /ForeignKey\s*\(\s*['"](\w+)\.(\w+)['"]\s*\)/.exec(args);
      if (fkMatch) {
        model.foreignKeys.push({ column: fieldName, referencedTable: fkMatch[1]! });
      }

      model.columns.push({ name: fieldName, type: sqlType, nullable, isPk, isUnique });
    }

    // Django: field = models.CharField(...)
    const djangoColPattern = /(\w+)\s*=\s*models\.(\w+Field|AutoField|BigAutoField)\s*\(([^)]*)\)/g;
    let djangoMatch: RegExpExecArray | null;
    while ((djangoMatch = djangoColPattern.exec(classBody)) !== null) {
      const fieldName = djangoMatch[1]!;
      const fieldType = djangoMatch[2]!;
      const args = djangoMatch[3] ?? "";

      if (fieldType === "ForeignKey" || fieldType === "OneToOneField") {
        const dbColMatch = /db_column\s*=\s*['"]([^'"]+)['"]/.exec(args);
        const colName = dbColMatch ? dbColMatch[1]! : fieldName + "_id";
        const toMatch = /to\s*=\s*['"]([^'"]+)['"]/.exec(args) ?? /^'([^']+)'/.exec(args.trim()) ?? /^"([^"]+)"/.exec(args.trim());
        if (toMatch) {
          model.foreignKeys.push({ column: colName, referencedTable: toMatch[1]!.toLowerCase() });
        }
        model.columns.push({ name: colName, type: "BIGINT", nullable: !args.includes("null=False"), isPk: false, isUnique: args.includes("unique=True") });
        continue;
      }
      if (fieldType === "ManyToManyField") continue; // skip join tables

      const sqlType = PY_TYPE_MAP[fieldType] ?? "VARCHAR";
      const isPk = fieldType === "AutoField" || fieldType === "BigAutoField";
      const nullable = !isPk && (args.includes("null=True") || !args.includes("null=False"));
      const isUnique = args.includes("unique=True");
      const dbColMatch = /db_column\s*=\s*['"]([^'"]+)['"]/.exec(args);
      const colName = dbColMatch ? dbColMatch[1]! : fieldName;

      model.columns.push({ name: colName, type: sqlType, nullable, isPk, isUnique });
    }

    if (model.columns.length > 0) {
      models.push(model);
    }
  }

  return models;
}

function findPythonFiles(repoRoot: string): string[] {
  const results: string[] = [];
  const skip = new Set(["node_modules", ".git", "__pycache__", ".venv", "venv", "env", "dist"]);
  function walk(dir: string) {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory() && !skip.has(entry.name)) walk(path.join(dir, entry.name));
      else if (entry.isFile() && entry.name.endsWith(".py")) results.push(path.join(dir, entry.name));
    }
  }
  walk(repoRoot);
  return results;
}

export class FrameworkSqlAlchemyExtractor implements ExtractorPlugin {
  readonly name = "framework-sqlalchemy";
  readonly languages = ["python"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;
    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    const pyFiles = findPythonFiles(repoRoot);
    if (pyFiles.length === 0) {
      log.info("[framework-sqlalchemy] no Python files found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const schemaId = buildUrn("schema", scope, "sa-schema");
    let modelCount = 0;

    for (const absPath of pyFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const models = parseSqlAlchemyFile(content);
      if (models.length === 0) continue;

      const checksum = sha256short(content);

      if (modelCount === 0) {
        nodes.push({
          id: schemaId, label: "SQLAlchemy/Django Schema", type: "DatabaseSchema", scope,
          source: "schema", source_checksum: sha256short(scope),
          confidence: 0.80, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { orm: "sqlalchemy" },
        });
      }

      for (const model of models) {
        modelCount++;
        const tableId = buildUrn("schema", scope, "sa-schema", model.tableName);
        nodes.push({
          id: tableId, label: model.tableName, type: "DatabaseTable", scope,
          source: "schema", source_checksum: checksum,
          confidence: 0.80, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { tableName: model.tableName, modelClass: model.className, orm: "sqlalchemy", sourceFile: relPath },
        });
        edges.push({ fromId: schemaId, toId: tableId, type: "has_table", source: "schema", confidence: 0.80 });

        for (const col of model.columns) {
          const colId = buildUrn("schema", scope, "sa-schema", model.tableName, col.name);
          nodes.push({
            id: colId, label: col.name, type: "DatabaseColumn", scope,
            source: "schema", source_checksum: checksum,
            confidence: 0.80, valid_from_commit: commitSha, valid_to_commit: null,
            properties: { columnName: col.name, columnType: col.type, nullable: col.nullable, isPrimaryKey: col.isPk, isUnique: col.isUnique },
          });
          edges.push({ fromId: tableId, toId: colId, type: "has_column", source: "schema", confidence: 0.80 });
        }

        for (const fk of model.foreignKeys) {
          const fkTargetId = buildUrn("schema", scope, "sa-schema", fk.referencedTable);
          edges.push({
            fromId: tableId, toId: fkTargetId, type: "references",
            source: "schema", confidence: 0.80,
            properties: { fromColumn: fk.column },
          });
        }
      }
    }

    if (modelCount === 0) {
      log.info("[framework-sqlalchemy] no SQLAlchemy/Django model classes found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-sqlalchemy] wrote ${nodesWritten} nodes, ${edgesWritten} edges (${modelCount} models)`);
    return { nodesWritten, edgesWritten };
  }
}
