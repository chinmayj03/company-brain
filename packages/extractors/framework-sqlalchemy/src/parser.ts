/**
 * Python ORM parser — handles both SQLAlchemy and Django models.
 *
 * All parsing is regex-based (no Python AST parser needed).
 *
 * SQLAlchemy:
 *   - Detects files importing sqlalchemy / flask_sqlalchemy
 *   - Parses classes inheriting from Base, db.Model, Model
 *   - Reads Column(...) / db.Column(...) field definitions
 *
 * Django:
 *   - Detects files importing from django.db
 *   - Parses classes inheriting from models.Model or Model
 *   - Reads models.CharField, models.ForeignKey, etc.
 */

import type { PythonOrmModel, PythonOrmField } from "./types.js";

// ─── Shared helpers ──────────────────────────────────────────────────────────

/** Convert PascalCase / CamelCase to snake_case */
function toSnakeCase(name: string): string {
  return name
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLowerCase();
}

/**
 * Extract the value of a keyword argument from an argument string.
 * e.g. extractKwarg('nullable=False, name="foo"', "name") → "foo"
 */
function extractKwarg(argStr: string, kwarg: string): string | null {
  // Quoted string value
  const quoted = new RegExp(`${kwarg}\\s*=\\s*["']([^"']*)["']`);
  const qm = argStr.match(quoted);
  if (qm) return qm[1];
  // Unquoted / boolean / numeric value
  const unquoted = new RegExp(`${kwarg}\\s*=\\s*([\\w.]+)`);
  const um = argStr.match(unquoted);
  return um ? um[1] : null;
}

function extractBoolKwarg(argStr: string, kwarg: string, defaultVal: boolean): boolean {
  const val = extractKwarg(argStr, kwarg);
  if (val === null) return defaultVal;
  return val.toLowerCase() === "true";
}

// ─── SQLAlchemy parser ───────────────────────────────────────────────────────

const SA_IMPORT_RE = /(?:^|\n)\s*(?:import sqlalchemy|from sqlalchemy|from flask_sqlalchemy)/;

const SA_BASE_CLASSES = ["Base", "db\\.Model", "Model", "DeclarativeBase"];

/** Map SQLAlchemy type tokens to SQL type strings */
function saTypeToDbType(typeToken: string): string {
  const t = typeToken.trim();
  if (/^String/i.test(t)) return "VARCHAR";
  if (/^Text/i.test(t)) return "TEXT";
  if (/^BigInteger/i.test(t)) return "BIGINT";
  if (/^Integer/i.test(t)) return "INTEGER";
  if (/^SmallInteger/i.test(t)) return "SMALLINT";
  if (/^Boolean/i.test(t)) return "BOOLEAN";
  if (/^Float/i.test(t)) return "FLOAT";
  if (/^Numeric|^Decimal/i.test(t)) return "DECIMAL";
  if (/^DateTime/i.test(t)) return "TIMESTAMP";
  if (/^Date/i.test(t)) return "DATE";
  if (/^Time/i.test(t)) return "TIME";
  if (/^UUID/i.test(t)) return "UUID";
  if (/^JSON/i.test(t)) return "JSON";
  if (/^LargeBinary|^BYTEA|^BLOB/i.test(t)) return "BYTEA";
  if (/^Enum/i.test(t)) return "VARCHAR";
  return "VARCHAR";
}

export function parseSqlAlchemyFile(content: string, filePath: string): PythonOrmModel[] {
  if (!SA_IMPORT_RE.test(content)) return [];

  const models: PythonOrmModel[] = [];
  const lines = content.split(/\r?\n/);

  const baseClassPattern = SA_BASE_CLASSES.join("|");
  const classRe = new RegExp(`^class\\s+(\\w+)\\s*\\(([^)]*(?:${baseClassPattern})[^)]*)\\)\\s*:`);

  let inClass = false;
  let className = "";
  let tableName = "";
  let classIndent = 0;
  let fields: PythonOrmField[] = [];

  const finalizeClass = () => {
    if (inClass && className) {
      models.push({ className, tableName, fields, orm: "sqlalchemy", sourceFile: filePath });
    }
    inClass = false;
    className = "";
    tableName = "";
    fields = [];
    classIndent = 0;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trimEnd();
    const stripped = line.trimStart();

    // Detect new class definition
    const classMatch = stripped.match(classRe);
    if (classMatch) {
      finalizeClass();
      className = classMatch[1];
      tableName = toSnakeCase(className);
      classIndent = rawLine.length - stripped.length;
      inClass = true;
      continue;
    }

    if (!inClass) continue;

    // Detect end of class by dedent (non-blank line at or before class indent)
    if (stripped.length > 0 && !stripped.startsWith("#")) {
      const currentIndent = rawLine.length - stripped.length;
      if (currentIndent <= classIndent && !stripped.startsWith("class")) {
        // Could be a new top-level definition — finalize current class
        finalizeClass();
        // Re-process this line as a potential class opener
        i--;
        continue;
      }
    }

    if (!stripped || stripped.startsWith("#")) continue;

    // __tablename__
    const tblMatch = stripped.match(/^__tablename__\s*=\s*["']([^"']+)["']/);
    if (tblMatch) {
      tableName = tblMatch[1];
      continue;
    }

    // Column definition: fieldname = Column(...) or fieldname = db.Column(...)
    // Also handles mapped_column for SQLAlchemy 2.x
    const colMatch = stripped.match(/^(\w+)\s*(?::\s*\w[^=]*)?\s*=\s*(?:db\.)?(?:Column|mapped_column)\s*\(/);
    if (colMatch) {
      const fieldName = colMatch[1];
      if (fieldName.startsWith("__")) continue;

      // Gather the full column definition (may span multiple lines)
      let colDef = stripped;
      let openParens = (colDef.match(/\(/g) || []).length - (colDef.match(/\)/g) || []).length;
      let j = i + 1;
      while (openParens > 0 && j < lines.length) {
        colDef += " " + lines[j].trim();
        openParens += (lines[j].match(/\(/g) || []).length - (lines[j].match(/\)/g) || []).length;
        j++;
      }

      // Extract the argument block inside Column(...)
      const argBlockMatch = colDef.match(/(?:db\.)?(?:Column|mapped_column)\s*\(([\s\S]*)\)/);
      const argBlock = argBlockMatch ? argBlockMatch[1] : "";

      // Detect type: first token that looks like a type name
      const typeTokenMatch = argBlock.match(/^\s*(\w+)\s*(?:\(|,|$)/);
      const dbType = typeTokenMatch ? saTypeToDbType(typeTokenMatch[1]) : "VARCHAR";

      const isPrimaryKey = /primary_key\s*=\s*True/i.test(argBlock);
      const nullable = isPrimaryKey ? false : extractBoolKwarg(argBlock, "nullable", true);
      const unique = extractBoolKwarg(argBlock, "unique", false);

      // ForeignKey detection: ForeignKey("other_table.col")
      const fkMatch = argBlock.match(/ForeignKey\s*\(\s*["']([^"']+)["']/);
      const isForeignKey = fkMatch !== null;
      const foreignKeyTarget = fkMatch ? fkMatch[1] : null;

      // name= kwarg overrides field name for db column name
      const nameAttr = extractKwarg(argBlock, "name");
      const dbColumnName = nameAttr ?? toSnakeCase(fieldName);

      const serverDefaultVal = extractKwarg(argBlock, "server_default");

      fields.push({
        name: fieldName,
        dbColumnName,
        dbType,
        nullable,
        isPrimaryKey,
        isForeignKey,
        foreignKeyTarget,
        serverDefault: serverDefaultVal,
        unique,
      });
    }
  }

  // Finalize last class
  finalizeClass();

  return models;
}

// ─── Django parser ───────────────────────────────────────────────────────────

const DJANGO_IMPORT_RE = /(?:^|\n)\s*(?:from django\.db import|import django\.db)/;

/** Map Django field type to SQL type */
function djangoFieldToDbType(fieldType: string): string {
  if (/CharField|EmailField|URLField|SlugField|IPAddressField|FileField|ImageField/i.test(fieldType)) return "VARCHAR";
  if (/TextField/i.test(fieldType)) return "TEXT";
  if (/BigAutoField|BigIntegerField|PositiveBigIntegerField/i.test(fieldType)) return "BIGINT";
  if (/SmallAutoField|SmallIntegerField|PositiveSmallIntegerField/i.test(fieldType)) return "SMALLINT";
  if (/AutoField|IntegerField|PositiveIntegerField/i.test(fieldType)) return "INTEGER";
  if (/BooleanField|NullBooleanField/i.test(fieldType)) return "BOOLEAN";
  if (/DecimalField/i.test(fieldType)) return "DECIMAL";
  if (/FloatField/i.test(fieldType)) return "FLOAT";
  if (/DateTimeField/i.test(fieldType)) return "TIMESTAMP";
  if (/DateField/i.test(fieldType)) return "DATE";
  if (/TimeField/i.test(fieldType)) return "TIME";
  if (/UUIDField/i.test(fieldType)) return "UUID";
  if (/JSONField|HStoreField/i.test(fieldType)) return "JSON";
  if (/BinaryField/i.test(fieldType)) return "BYTEA";
  if (/ForeignKey|OneToOneField/i.test(fieldType)) return "INTEGER"; // FK col — usually int
  if (/ManyToManyField/i.test(fieldType)) return "_M2M_SKIP_";
  return "VARCHAR";
}

export function parseDjangoFile(content: string, filePath: string): PythonOrmModel[] {
  if (!DJANGO_IMPORT_RE.test(content)) return [];

  const models: PythonOrmModel[] = [];
  const lines = content.split(/\r?\n/);

  // Class pattern: class Foo(models.Model): or class Foo(Model):
  const classRe = /^class\s+(\w+)\s*\(\s*(?:models\.Model|Model)\s*\)\s*:/;

  let inClass = false;
  let className = "";
  let tableName = "";
  let classIndent = 0;
  let fields: PythonOrmField[] = [];
  let inMeta = false;
  let metaIndent = 0;

  const finalizeClass = () => {
    if (inClass && className) {
      models.push({ className, tableName, fields, orm: "django", sourceFile: filePath });
    }
    inClass = false;
    inMeta = false;
    className = "";
    tableName = "";
    fields = [];
    classIndent = 0;
    metaIndent = 0;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trimEnd();
    const stripped = line.trimStart();

    // Detect new class definition
    const classMatch = stripped.match(classRe);
    if (classMatch) {
      finalizeClass();
      className = classMatch[1];
      tableName = toSnakeCase(className);
      classIndent = rawLine.length - stripped.length;
      inClass = true;
      continue;
    }

    if (!inClass) continue;

    // Detect class end by dedent
    if (stripped.length > 0 && !stripped.startsWith("#")) {
      const currentIndent = rawLine.length - stripped.length;
      if (currentIndent <= classIndent) {
        finalizeClass();
        i--;
        continue;
      }
    }

    if (!stripped || stripped.startsWith("#")) continue;

    // Detect Meta inner class
    if (stripped.match(/^class\s+Meta\s*:/)) {
      inMeta = true;
      metaIndent = rawLine.length - stripped.length;
      continue;
    }

    if (inMeta) {
      const currentIndent = rawLine.length - stripped.length;
      if (currentIndent <= metaIndent && stripped.length > 0) {
        inMeta = false;
        // Fall through to process this line
      } else {
        // db_table
        const dbTableMatch = stripped.match(/db_table\s*=\s*["']([^"']+)["']/);
        if (dbTableMatch) tableName = dbTableMatch[1];
        continue;
      }
    }

    // Field definition: fieldname = models.SomeField(...)
    const fieldMatch = stripped.match(/^(\w+)\s*=\s*models\.(\w+)\s*\(/);
    if (fieldMatch) {
      const fieldName = fieldMatch[1];
      const fieldType = fieldMatch[2];

      const dbType = djangoFieldToDbType(fieldType);

      // Skip ManyToMany (junction table — not a column in this table)
      if (dbType === "_M2M_SKIP_") continue;

      // Gather full field definition
      let fieldDef = stripped;
      let openParens = (fieldDef.match(/\(/g) || []).length - (fieldDef.match(/\)/g) || []).length;
      let j = i + 1;
      while (openParens > 0 && j < lines.length) {
        fieldDef += " " + lines[j].trim();
        openParens += (lines[j].match(/\(/g) || []).length - (lines[j].match(/\)/g) || []).length;
        j++;
      }

      // Extract argument block
      const argBlockMatch = fieldDef.match(/models\.\w+\s*\(([\s\S]*)\)/);
      const argBlock = argBlockMatch ? argBlockMatch[1] : "";

      // AutoField variants are implicit PKs
      const isPrimaryKey = /AutoField/i.test(fieldType);
      const isNullTrue = /null\s*=\s*True/i.test(argBlock);
      const nullable = isPrimaryKey ? false : isNullTrue;
      const unique = /unique\s*=\s*True/i.test(argBlock);

      // ForeignKey / OneToOneField
      const isForeignKey = /^(?:ForeignKey|OneToOneField)$/i.test(fieldType);
      let foreignKeyTarget: string | null = null;
      if (isForeignKey) {
        // ForeignKey("OtherModel", ...) or ForeignKey(OtherModel, ...)
        const fkTargetMatch = argBlock.match(/^\s*["']?(\w+)["']?\s*,/);
        if (fkTargetMatch) {
          foreignKeyTarget = toSnakeCase(fkTargetMatch[1]);
        }
      }

      // db_column= kwarg
      const dbColumnAttr = extractKwarg(argBlock, "db_column");
      // Django FK columns get _id suffix by convention unless db_column is explicit
      const dbColumnName = dbColumnAttr
        ? dbColumnAttr
        : isForeignKey
          ? toSnakeCase(fieldName) + "_id"
          : toSnakeCase(fieldName);

      fields.push({
        name: fieldName,
        dbColumnName,
        dbType,
        nullable,
        isPrimaryKey,
        isForeignKey,
        foreignKeyTarget,
        serverDefault: null,
        unique,
      });
    }
  }

  finalizeClass();

  return models;
}

// ─── Combined entry point ────────────────────────────────────────────────────

export function parseOrmFile(content: string, filePath: string): PythonOrmModel[] {
  // Try SQLAlchemy first
  const saModels = parseSqlAlchemyFile(content, filePath);
  if (saModels.length > 0) return saModels;

  // Fall back to Django
  return parseDjangoFile(content, filePath);
}
