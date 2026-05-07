import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

interface PrismaModel { name: string; tableName: string; fields: Array<{ name: string; type: string; isOptional: boolean; isId: boolean; isUnique: boolean; }> }

function parsePrismaSchema(content: string): PrismaModel[] {
  const models: PrismaModel[] = [];
  const modelRegex = /model\s+(\w+)\s*\{([^}]+)\}/g;
  let m: RegExpExecArray | null;
  while ((m = modelRegex.exec(content)) !== null) {
    const modelName = m[1]!;
    const body = m[2]!;
    const tableMatch = /@@map\("([^"]+)"\)/.exec(body);
    const tableName = tableMatch ? tableMatch[1]! : modelName.replace(/([A-Z])/g, (_, c, i) => i > 0 ? `_${c.toLowerCase()}` : c.toLowerCase());
    const fields: PrismaModel["fields"] = [];
    for (const line of body.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("//") || trimmed.startsWith("@@") || trimmed.startsWith("@")) continue;
      const fieldMatch = /^(\w+)\s+(\w+)(\?)?/.exec(trimmed);
      if (!fieldMatch) continue;
      const [, fname, ftype, optional] = fieldMatch;
      if (["String","Int","Float","Boolean","DateTime","Json","Bytes","BigInt","Decimal"].includes(ftype!) ||
          /^[A-Z]/.test(ftype!)) {
        fields.push({
          name: fname!, type: ftype!, isOptional: !!optional,
          isId: trimmed.includes("@id"), isUnique: trimmed.includes("@unique"),
        });
      }
    }
    models.push({ name: modelName, tableName, fields });
  }
  return models;
}

export class FrameworkPrismaExtractor implements ExtractorPlugin {
  readonly name = "framework-prisma";
  readonly languages = ["prisma"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;
    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    // Find all .prisma files
    const prismaFiles: string[] = [];
    function walk(dir: string) {
      if (!fs.existsSync(dir)) return;
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory() && !["node_modules",".git","dist"].includes(entry.name)) walk(path.join(dir, entry.name));
        else if (entry.isFile() && entry.name.endsWith(".prisma")) prismaFiles.push(path.join(dir, entry.name));
      }
    }
    walk(repoRoot);

    if (prismaFiles.length === 0) {
      log.info("[framework-prisma] no .prisma files found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    for (const absPath of prismaFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const checksum = sha256short(content);

      const schemaId = buildUrn("schema", scope, relPath);
      nodes.push({
        id: schemaId, label: relPath, type: "DatabaseSchema", scope,
        source: "schema", source_checksum: checksum,
        confidence: 0.95, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { path: relPath, orm: "prisma" },
      });

      for (const model of parsePrismaSchema(content)) {
        const tableId = buildUrn("schema", scope, relPath, model.tableName);
        nodes.push({
          id: tableId, label: model.tableName, type: "DatabaseTable", scope,
          source: "schema", source_checksum: checksum,
          confidence: 0.95, valid_from_commit: commitSha, valid_to_commit: null,
          properties: { tableName: model.tableName, modelName: model.name, orm: "prisma" },
        });
        edges.push({ fromId: schemaId, toId: tableId, type: "has_table", source: "schema", confidence: 0.95 });

        for (const field of model.fields) {
          const colId = buildUrn("schema", scope, relPath, model.tableName, field.name);
          nodes.push({
            id: colId, label: field.name, type: "DatabaseColumn", scope,
            source: "schema", source_checksum: checksum,
            confidence: 0.95, valid_from_commit: commitSha, valid_to_commit: null,
            properties: { columnName: field.name, columnType: field.type, nullable: field.isOptional, isPrimaryKey: field.isId, isUnique: field.isUnique },
          });
          edges.push({ fromId: tableId, toId: colId, type: "has_column", source: "schema", confidence: 0.95 });
        }
      }
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-prisma] wrote ${nodesWritten} nodes, ${edgesWritten} edges`);
    return { nodesWritten, edgesWritten };
  }
}
