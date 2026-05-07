/**
 * Java JPA/Hibernate @Entity parser.
 *
 * Uses regex-based parsing (no Java AST parser needed).
 * Returns null if the file does not contain @Entity.
 *
 * Handles:
 * - @Entity, @Table(name="...", schema="...")
 * - @Id, @GeneratedValue
 * - @Column(name="...", nullable=false, length=255, unique=true, columnDefinition="...")
 * - @ManyToOne, @OneToOne → foreign key columns
 * - @JoinColumn(name="...", referencedColumnName="...")
 * - @OneToMany, @ManyToMany → skipped (no column in this table)
 * - @Transient → skipped
 * - @Enumerated → VARCHAR
 * - Java type → SQL type mapping
 * - camelCase → snake_case for unspecified column names
 */

import type { JpaEntity, JpaField } from "./types.js";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Convert camelCase / PascalCase to snake_case */
function toSnakeCase(name: string): string {
  return name
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLowerCase();
}

/** Map a Java type string to a SQL type string */
function javaTypeToDbType(javaType: string, isEnumerated: boolean): string {
  if (isEnumerated) return "VARCHAR";
  const t = javaType.trim();
  if (t === "String") return "VARCHAR";
  if (t === "Long" || t === "long") return "BIGINT";
  if (t === "Integer" || t === "int") return "INTEGER";
  if (t === "Boolean" || t === "boolean") return "BOOLEAN";
  if (t === "Double" || t === "double" || t === "Float" || t === "float" || t === "BigDecimal") return "DECIMAL";
  if (t === "LocalDate") return "DATE";
  if (t === "LocalDateTime" || t === "ZonedDateTime" || t === "Instant") return "TIMESTAMP";
  if (t === "UUID") return "UUID";
  if (t === "byte[]") return "BYTEA";
  // Unknown / custom types default to VARCHAR
  return "VARCHAR";
}

/** Extract a named attribute from an annotation attribute string, e.g. name = "foo" → "foo" */
function extractAnnotationAttr(attrBlock: string, attrName: string): string | null {
  // handles: name = "value"  or  name="value"
  const re = new RegExp(`${attrName}\\s*=\\s*"([^"]*)"`, "i");
  const m = attrBlock.match(re);
  return m ? m[1] : null;
}

/** Extract a boolean attribute, e.g. nullable = false */
function extractBoolAttr(attrBlock: string, attrName: string, defaultVal: boolean): boolean {
  const re = new RegExp(`${attrName}\\s*=\\s*(true|false)`, "i");
  const m = attrBlock.match(re);
  if (!m) return defaultVal;
  return m[1].toLowerCase() === "true";
}

/** Extract an integer attribute, e.g. length = 255 */
function extractIntAttr(attrBlock: string, attrName: string): number | null {
  const re = new RegExp(`${attrName}\\s*=\\s*(\\d+)`, "i");
  const m = attrBlock.match(re);
  return m ? parseInt(m[1], 10) : null;
}

/**
 * Extract the parenthesised content of a single annotation,
 * handling multi-line spans. E.g. "@Column( name = "foo",\n  nullable = false )" → ' name = "foo",\n  nullable = false '
 */
function extractAnnotationBody(annotationLines: string, annotationName: string): string | null {
  const re = new RegExp(`@${annotationName}\\s*\\(([^)]*(?:\\([^)]*\\)[^)]*)*)\\)`, "s");
  const m = annotationLines.match(re);
  return m ? m[1] : null;
}

// ─── Parser ──────────────────────────────────────────────────────────────────

export function parseJavaFile(content: string, filePath: string): JpaEntity | null {
  // Quick check: must have @Entity
  if (!content.includes("@Entity")) return null;

  // ── Class-level annotations ──────────────────────────────────────────────

  // Extract class name
  const classMatch = content.match(/(?:public|protected|private)?\s*class\s+(\w+)/);
  if (!classMatch) return null;
  const className = classMatch[1];

  // @Table annotation attributes
  let tableName = toSnakeCase(className);
  let schemaName = "";

  const tableAnnotationMatch = content.match(/@Table\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)/s);
  if (tableAnnotationMatch) {
    const tableBody = tableAnnotationMatch[1];
    const nameAttr = extractAnnotationAttr(tableBody, "name");
    if (nameAttr) tableName = nameAttr;
    const schemaAttr = extractAnnotationAttr(tableBody, "schema");
    if (schemaAttr) schemaName = schemaAttr;
  }

  // ── Field extraction ─────────────────────────────────────────────────────

  // Find the class body (everything after the first opening brace)
  const classBodyStart = content.indexOf("{");
  if (classBodyStart === -1) return null;
  const classBody = content.slice(classBodyStart + 1);

  // Split into lines and process field blocks
  // A "field block" is one or more annotation lines followed by a field declaration line.
  const lines = classBody.split(/\r?\n/);

  const fields: JpaField[] = [];

  // Collect annotation lines until we hit a field declaration
  let pendingAnnotations: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();

    // Skip blank lines and comments, but flush pending annotations
    if (line === "" || line.startsWith("//") || line.startsWith("*") || line.startsWith("/*")) {
      // Don't flush here — blank lines can appear between annotations in some Java styles
      continue;
    }

    // Detect annotation line
    if (line.startsWith("@")) {
      pendingAnnotations.push(line);
      continue;
    }

    // Detect field declaration: modifiers type fieldName;
    // e.g.: private String name;  /  protected Long id;  /  private MyEnum status;
    const fieldMatch = line.match(
      /^(?:(?:private|protected|public|static|final|volatile|transient)\s+)*?([\w<>[\],\s.]+?)\s+(\w+)\s*;/
    );
    if (fieldMatch) {
      const javaType = fieldMatch[1].trim();
      const javaName = fieldMatch[2];

      const annotationBlock = pendingAnnotations.join("\n");
      pendingAnnotations = [];

      // Skip @Transient fields
      if (annotationBlock.includes("@Transient")) continue;

      // Skip @OneToMany and @ManyToMany (no column in this table)
      if (annotationBlock.includes("@OneToMany") || annotationBlock.includes("@ManyToMany")) continue;

      // Flags
      const isPrimaryKey = annotationBlock.includes("@Id");
      const isGeneratedValue = annotationBlock.includes("@GeneratedValue");
      const isForeignKey = annotationBlock.includes("@ManyToOne") || annotationBlock.includes("@OneToOne");
      const isEnumerated = annotationBlock.includes("@Enumerated");

      // @Column attributes
      let dbColumnName = isForeignKey
        ? toSnakeCase(javaName) + "_id"  // default FK column convention
        : toSnakeCase(javaName);
      let nullable = true;
      let unique = false;
      let columnLength: number | null = null;
      let defaultValue: string | null = null;

      const columnBody = extractAnnotationBody(annotationBlock, "Column");
      if (columnBody !== null) {
        const nameAttr = extractAnnotationAttr(columnBody, "name");
        if (nameAttr) dbColumnName = nameAttr;
        nullable = extractBoolAttr(columnBody, "nullable", true);
        unique = extractBoolAttr(columnBody, "unique", false);
        columnLength = extractIntAttr(columnBody, "length");
        const colDef = extractAnnotationAttr(columnBody, "columnDefinition");
        if (colDef) defaultValue = colDef;
      }

      // @JoinColumn attributes
      let joinColumnName: string | null = null;
      let referencedColumn: string | null = null;
      const joinBody = extractAnnotationBody(annotationBlock, "JoinColumn");
      if (joinBody !== null) {
        joinColumnName = extractAnnotationAttr(joinBody, "name");
        referencedColumn = extractAnnotationAttr(joinBody, "referencedColumnName");
        if (joinColumnName) dbColumnName = joinColumnName;
      }

      // For FK fields, referencedEntity is the Java type of the field
      const referencedEntity = isForeignKey ? javaType : null;

      const dbType = javaTypeToDbType(javaType, isEnumerated);

      fields.push({
        javaName,
        dbColumnName,
        javaType,
        dbType,
        nullable,
        isPrimaryKey,
        isGeneratedValue,
        isForeignKey,
        referencedEntity,
        referencedColumn,
        joinColumnName,
        columnLength,
        unique,
        defaultValue,
      });
    } else {
      // Not a field declaration — reset pending annotations
      // (could be a method, constructor, etc.)
      pendingAnnotations = [];
    }
  }

  return { className, tableName, schemaName, fields, sourceFile: filePath };
}
