export interface SqlColumn {
  name: string;
  dbName: string;          // same as name for SQL (no aliasing)
  dataType: string;        // VARCHAR, INTEGER, BIGINT, TIMESTAMP, etc. — normalized
  nullable: boolean;       // DEFAULT true unless NOT NULL
  isPrimaryKey: boolean;
  isForeignKey: boolean;
  defaultValue: string | null;
  rawType: string;         // original type string from SQL
}

export interface SqlForeignKey {
  fromColumn: string;
  toTable: string;
  toColumn: string;
  onDelete: string;        // CASCADE, SET NULL, RESTRICT, NO ACTION
}

export interface SqlIndex {
  name: string;
  columns: string[];
  unique: boolean;
}

export interface SqlTable {
  name: string;            // logical name (normalised: strip schema prefix)
  schemaName: string;      // e.g. "public", "dbo", "" if none
  columns: SqlColumn[];
  foreignKeys: SqlForeignKey[];
  indexes: SqlIndex[];
  sourceFile: string;      // relative path of the SQL file
}

export interface SqlParseResult {
  tables: SqlTable[];
  warnings: string[];
}
