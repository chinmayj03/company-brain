/** Minimal SQL DDL parser. Supports CREATE TABLE, ALTER TABLE ADD COLUMN/CONSTRAINT. */

export interface SqlColumn {
  name: string;
  type: string;
  nullable: boolean;
  isPrimaryKey: boolean;
  isUnique: boolean;
  defaultValue?: string;
}

export interface SqlForeignKey {
  constraintName?: string;
  column: string;
  referencedTable: string;
  referencedColumn: string;
}

export interface SqlTable {
  tableName: string;
  columns: SqlColumn[];
  foreignKeys: SqlForeignKey[];
  sourceFile: string;
}

/** Remove SQL comments from a string */
function stripComments(sql: string): string {
  // Remove block comments /* ... */
  sql = sql.replace(/\/\*[\s\S]*?\*\//g, " ");
  // Remove line comments -- ...
  sql = sql.replace(/--[^\n]*/g, " ");
  return sql;
}

/** Normalize whitespace */
function normalize(sql: string): string {
  return stripComments(sql).replace(/\s+/g, " ").trim();
}

/** Map SQL type tokens to a normalized form */
function normalizeType(raw: string): string {
  const t = raw.toUpperCase().replace(/\s*\([^)]*\)/g, "").trim();
  const map: Record<string, string> = {
    "CHARACTER VARYING": "VARCHAR", "CHARACTER": "CHAR",
    "INT": "INTEGER", "INT2": "SMALLINT", "INT4": "INTEGER", "INT8": "BIGINT",
    "BOOL": "BOOLEAN", "FLOAT4": "REAL", "FLOAT8": "DOUBLE PRECISION",
    "SERIAL": "INTEGER", "BIGSERIAL": "BIGINT", "SMALLSERIAL": "SMALLINT",
    "TEXT": "TEXT", "BYTEA": "BYTEA", "JSONB": "JSONB", "JSON": "JSON",
    "UUID": "UUID", "TIMESTAMP": "TIMESTAMP", "TIMESTAMPTZ": "TIMESTAMPTZ",
    "DATE": "DATE", "TIME": "TIME", "NUMERIC": "NUMERIC", "DECIMAL": "DECIMAL",
    "TINYINT": "TINYINT", "MEDIUMINT": "INTEGER", "LONGTEXT": "TEXT",
    "DATETIME": "TIMESTAMP", "ENUM": "VARCHAR",
  };
  return map[t] ?? raw.toUpperCase();
}

export function parseSqlFile(content: string, sourceFile: string): SqlTable[] {
  const tables: SqlTable[] = [];
  const tableMap = new Map<string, SqlTable>();

  const sql = normalize(content);
  // Split on semicolons to get individual statements
  const statements = sql.split(";").map(s => s.trim()).filter(Boolean);

  for (const stmt of statements) {
    const upper = stmt.toUpperCase();

    // CREATE TABLE
    const createMatch = /CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`?"?(\w+)`?"?\.)?\`?"?(\w+)`?"?\s*\((.+)\)/.exec(stmt);
    if (createMatch) {
      const tableName = (createMatch[2] ?? "").toLowerCase();
      if (!tableName) continue;

      const table: SqlTable = { tableName, columns: [], foreignKeys: [], sourceFile };
      tableMap.set(tableName, table);
      tables.push(table);

      const body = createMatch[3] ?? "";
      parseTableBody(body, table);
      continue;
    }

    // ALTER TABLE ... ADD COLUMN
    const alterAddCol = /ALTER\s+TABLE\s+(?:\w+\.)?`?"?(\w+)`?"?\s+ADD\s+(?:COLUMN\s+)?`?"?(\w+)`?"?\s+(\w+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)/i.exec(stmt);
    if (alterAddCol) {
      const tableName = (alterAddCol[1] ?? "").toLowerCase();
      const colName = (alterAddCol[2] ?? "").toLowerCase();
      const colType = normalizeType(alterAddCol[3] ?? "");
      const table = tableMap.get(tableName);
      if (table) {
        table.columns.push({ name: colName, type: colType, nullable: true, isPrimaryKey: false, isUnique: false });
      }
      continue;
    }

    // ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY
    const alterAddFk = /ALTER\s+TABLE\s+(?:\w+\.)?`?"?(\w+)`?"?\s+ADD\s+(?:CONSTRAINT\s+`?"?(\w+)`?"?\s+)?FOREIGN\s+KEY\s*\(\s*`?"?(\w+)`?"?\s*\)\s*REFERENCES\s+(?:\w+\.)?`?"?(\w+)`?"?\s*\(\s*`?"?(\w+)`?"?\s*\)/i.exec(stmt);
    if (alterAddFk) {
      const tableName = (alterAddFk[1] ?? "").toLowerCase();
      const table = tableMap.get(tableName);
      if (table) {
        table.foreignKeys.push({
          constraintName: alterAddFk[2],
          column: (alterAddFk[3] ?? "").toLowerCase(),
          referencedTable: (alterAddFk[4] ?? "").toLowerCase(),
          referencedColumn: (alterAddFk[5] ?? "").toLowerCase(),
        });
      }
    }
  }

  return tables;
}

function parseTableBody(body: string, table: SqlTable): void {
  // Split on commas, but be careful about commas inside parentheses (e.g. DECIMAL(10,2))
  const defs: string[] = [];
  let depth = 0, current = "";
  for (const ch of body) {
    if (ch === "(") { depth++; current += ch; }
    else if (ch === ")") { depth--; current += ch; }
    else if (ch === "," && depth === 0) { defs.push(current.trim()); current = ""; }
    else { current += ch; }
  }
  if (current.trim()) defs.push(current.trim());

  // Track primary key columns from TABLE-level PRIMARY KEY(...)
  const pkColumns = new Set<string>();
  for (const def of defs) {
    const pkMatch = /^(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)/i.exec(def);
    if (pkMatch) {
      for (const col of (pkMatch[1] ?? "").split(",")) {
        pkColumns.add(col.trim().replace(/[`"]/g, "").toLowerCase());
      }
      continue;
    }
    // TABLE-level UNIQUE
    const uqMatch = /^(?:CONSTRAINT\s+\w+\s+)?UNIQUE\s*(?:KEY\s+\w+\s*)?\(([^)]+)\)/i.exec(def);
    if (uqMatch) continue; // skip for simplicity

    // TABLE-level FOREIGN KEY
    const fkMatch = /^(?:CONSTRAINT\s+`?"?(\w+)`?"?\s+)?FOREIGN\s+KEY\s*\(\s*`?"?(\w+)`?"?\s*\)\s*REFERENCES\s+(?:\w+\.)?`?"?(\w+)`?"?\s*\(\s*`?"?(\w+)`?"?\s*\)/i.exec(def);
    if (fkMatch) {
      table.foreignKeys.push({
        constraintName: fkMatch[1],
        column: (fkMatch[2] ?? "").toLowerCase(),
        referencedTable: (fkMatch[3] ?? "").toLowerCase(),
        referencedColumn: (fkMatch[4] ?? "").toLowerCase(),
      });
      continue;
    }

    // Column definition: name type [modifiers]
    const colMatch = /^`?"?(\w+)`?"?\s+(\w+(?:\s*\(\s*[\d,\s]+\s*\))?(?:\s+\w+)?)/i.exec(def);
    if (!colMatch || /^(PRIMARY|UNIQUE|INDEX|KEY|CONSTRAINT|CHECK|FULLTEXT|SPATIAL)/i.test(def)) continue;
    const colName = (colMatch[1] ?? "").toLowerCase();
    const rawType = colMatch[2] ?? "";
    const typeToken = rawType.replace(/\s*\(.*\)/, "").trim();
    const colType = normalizeType(typeToken);
    const upper = def.toUpperCase();
    const isNotNull = upper.includes("NOT NULL");
    const isPk = upper.includes("PRIMARY KEY") || pkColumns.has(colName);
    const isUnique = upper.includes("UNIQUE");
    const defaultMatch = /DEFAULT\s+('(?:[^'\\]|\\.)*'|\S+)/i.exec(def);

    table.columns.push({
      name: colName, type: colType,
      nullable: !isNotNull && !isPk,
      isPrimaryKey: isPk, isUnique,
      defaultValue: defaultMatch ? defaultMatch[1] : undefined,
    });
  }

  // Apply table-level PK flags
  for (const col of table.columns) {
    if (pkColumns.has(col.name)) col.isPrimaryKey = true;
  }
}
