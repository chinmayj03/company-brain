/**
 * Internal types for the Prisma schema extractor.
 */

export type ColumnType =
  | "String" | "Int" | "BigInt" | "Float" | "Decimal"
  | "Boolean" | "DateTime" | "Json" | "Bytes" | "Unsupported"
  | string; // custom scalars

export interface PrismaField {
  name: string;
  /** Prisma scalar type or relation model name */
  type: ColumnType;
  isOptional: boolean;
  isArray: boolean;
  isPrimaryKey: boolean;
  isForeignKey: boolean;
  defaultValue: string | null;
  /** The model this field references (if it's a relation field) */
  relatedModel: string | null;
  /** @map("db_name") override */
  dbName: string | null;
  /** @unique attribute */
  isUnique: boolean;
  /** @updatedAt attribute */
  isUpdatedAt: boolean;
}

export interface PrismaIndex {
  name: string | null;
  fields: string[];
  isUnique: boolean;
  /** @@id, @@unique, @@index */
  kind: "id" | "unique" | "index";
}

export interface PrismaModel {
  name: string;
  /** @map("db_table_name") override */
  dbName: string | null;
  fields: PrismaField[];
  indexes: PrismaIndex[];
}

export interface PrismaEnum {
  name: string;
  values: string[];
}

export interface PrismaSchema {
  /** Path to the schema.prisma file (repo-relative) */
  filePath: string;
  /** Stem of the filename for URN construction */
  filenameStem: string;
  /** postgresql | mysql | sqlite | sqlserver | mongodb | cockroachdb */
  provider: string | null;
  models: PrismaModel[];
  enums: PrismaEnum[];
}
