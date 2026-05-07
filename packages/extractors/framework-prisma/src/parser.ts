/**
 * Prisma SDL parser.
 *
 * Parses schema.prisma files line by line — no external parser needed.
 * Handles: datasource, model, enum blocks with fields, attributes, and @@directives.
 *
 * Limitations (intentional for v1):
 * - No generator block parsing (not needed for graph nodes)
 * - No @@schema for multi-schema setups (deferred to v2)
 * - Relation fields are detected but not deeply resolved
 */

import type { PrismaSchema, PrismaModel, PrismaField, PrismaEnum, PrismaIndex, ColumnType } from "./types.js";

const SCALAR_TYPES = new Set([
  "String", "Int", "BigInt", "Float", "Decimal",
  "Boolean", "DateTime", "Json", "Bytes", "Unsupported",
]);

/** Parse a @default(...) value out of a field attribute string */
function parseDefault(attrText: string): string | null {
  const m = attrText.match(/@default\(([^)]+)\)/);
  return m ? m[1].trim() : null;
}

/** Parse a @map("name") value */
function parseMap(attrText: string): string | null {
  const m = attrText.match(/@map\("([^"]+)"\)/);
  return m ? m[1] : null;
}

/** Parse @@map("name") */
function parseBlockMap(line: string): string | null {
  const m = line.match(/@@map\("([^"]+)"\)/);
  return m ? m[1] : null;
}

/** Parse a @relation(...) and extract the model name it references */
function parseRelation(fieldType: string, attrText: string): string | null {
  // Relation fields: the type IS the model name (not in SCALAR_TYPES)
  // We just return the base type name
  return null; // resolved by caller based on type name
}

/** Parse @@index or @@unique or @@id block-level directive */
function parseBlockIndex(line: string): PrismaIndex | null {
  const idxMatch = line.match(/@@(index|unique|id)\(\[([^\]]+)\](?:,\s*name:\s*"([^"]+)")?\)/);
  if (!idxMatch) return null;
  const kind = idxMatch[1] as "index" | "unique" | "id";
  const fields = idxMatch[2].split(",").map((f) => f.trim().replace(/"/g, ""));
  const name = idxMatch[3] ?? null;
  return { name, fields, isUnique: kind === "unique" || kind === "id", kind };
}

type BlockType = "model" | "enum" | "datasource" | "generator" | null;

export function parsePrismaSchema(content: string, filePath: string): PrismaSchema {
  const filenameStem = filePath.split("/").pop()!.replace(/\.prisma$/, "");
  const lines = content.split(/\r?\n/);

  const models: PrismaModel[] = [];
  const enums: PrismaEnum[] = [];
  let provider: string | null = null;

  let blockType: BlockType = null;
  let currentModel: PrismaModel | null = null;
  let currentEnum: PrismaEnum | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    // Skip comments
    if (line.startsWith("//") || line.startsWith("/*") || line === "") continue;

    // Block openers
    if (!blockType) {
      const modelMatch = line.match(/^model\s+(\w+)\s*\{/);
      if (modelMatch) {
        blockType = "model";
        currentModel = { name: modelMatch[1], dbName: null, fields: [], indexes: [] };
        continue;
      }
      const enumMatch = line.match(/^enum\s+(\w+)\s*\{/);
      if (enumMatch) {
        blockType = "enum";
        currentEnum = { name: enumMatch[1], values: [] };
        continue;
      }
      const datasourceMatch = line.match(/^datasource\s+\w+\s*\{/);
      if (datasourceMatch) { blockType = "datasource"; continue; }
      const generatorMatch = line.match(/^generator\s+\w+\s*\{/);
      if (generatorMatch) { blockType = "generator"; continue; }
      continue;
    }

    // Block closer
    if (line === "}") {
      if (blockType === "model" && currentModel) {
        models.push(currentModel);
        currentModel = null;
      } else if (blockType === "enum" && currentEnum) {
        enums.push(currentEnum);
        currentEnum = null;
      }
      blockType = null;
      continue;
    }

    // Parse datasource
    if (blockType === "datasource") {
      const providerMatch = line.match(/provider\s*=\s*"([^"]+)"/);
      if (providerMatch) provider = providerMatch[1];
      continue;
    }

    // Skip generator content
    if (blockType === "generator") continue;

    // Parse enum values
    if (blockType === "enum" && currentEnum) {
      if (!line.startsWith("@@") && !line.startsWith("//")) {
        const valueName = line.split(/\s+/)[0];
        if (valueName) currentEnum.values.push(valueName);
      }
      continue;
    }

    // Parse model fields and directives
    if (blockType === "model" && currentModel) {
      // @@map
      const blockMap = parseBlockMap(line);
      if (blockMap) { currentModel.dbName = blockMap; continue; }

      // @@index / @@unique / @@id
      const blockIdx = parseBlockIndex(line);
      if (blockIdx) { currentModel.indexes.push(blockIdx); continue; }

      // Skip other @@ directives
      if (line.startsWith("@@")) continue;

      // Field line: fieldName  FieldType  attributes...
      const fieldMatch = line.match(/^(\w+)\s+([\w[\]?!]+)(.*)?$/);
      if (!fieldMatch) continue;

      const fieldName = fieldMatch[1];
      const rawType = fieldMatch[2];
      const attrText = fieldMatch[3] ?? "";

      // Parse type: Type? → optional, Type[] → array
      const isOptional = rawType.endsWith("?");
      const isArray = rawType.endsWith("[]");
      const baseType = rawType.replace(/[?[\]!]/g, "") as ColumnType;

      const isPrimaryKey = attrText.includes("@id");
      const isUnique = attrText.includes("@unique");
      const isUpdatedAt = attrText.includes("@updatedAt");
      const defaultValue = parseDefault(attrText);
      const dbName = parseMap(attrText);

      // Relation fields: type is not a scalar → it's another model
      const isRelation = !SCALAR_TYPES.has(baseType) && baseType !== "Unsupported";
      const isForeignKey = isRelation && !isArray; // single-side of a relation
      const relatedModel = isRelation ? baseType : null;

      currentModel.fields.push({
        name: fieldName,
        type: baseType,
        isOptional,
        isArray,
        isPrimaryKey,
        isForeignKey,
        defaultValue,
        relatedModel,
        dbName,
        isUnique,
        isUpdatedAt,
      });
    }
  }

  return { filePath, filenameStem, provider, models, enums };
}
