/**
 * SQL migration file scanner.
 *
 * Finds all SQL DDL migration files in a repository, covering:
 *   - Flyway (src/main/resources/db/migration/V*.sql)
 *   - Liquibase (src/main/resources/db/changelog/)
 *   - Rails db/structure.sql, db/migrate/
 *   - Alembic alembic/versions/, migrations/
 *   - Django database/migrations/
 *   - Prisma prisma/migrations/
 *   - Generic *.sql fallback (filtered by isDdlFile)
 */

import fs from "fs";
import path from "path";

/**
 * Priority-ordered list of well-known migration directories.
 * Checked first before the generic glob fallback.
 */
const KNOWN_MIGRATION_DIRS = [
  // Flyway (Java)
  "src/main/resources/db/migration",
  // Liquibase
  "src/main/resources/db/changelog",
  // Rails
  "db/migrate",
  // Rails SQL schema (single file)
  "db",
  // Generic / Alembic
  "migrations",
  "alembic/versions",
  // Django
  "database/migrations",
  // Prisma raw SQL output
  "prisma/migrations",
];

/**
 * Returns true if a filename matches known migration file naming conventions.
 * Flyway: V1__description.sql, R__repeatable.sql
 * Alembic: <hash>_description.sql
 * Rails: YYYYMMDDHHMMSS_description.sql or structure.sql
 * Generic: anything.sql
 */
function isMigrationFilename(filename: string): boolean {
  if (!filename.endsWith(".sql")) return false;
  // structure.sql (Rails)
  if (filename === "structure.sql") return true;
  // Flyway: V1__init.sql or R__repeatable.sql
  if (/^[VvRr]\d*__/.test(filename)) return true;
  // Rails timestamp: 20210101120000_create_users.sql
  if (/^\d{14}_/.test(filename)) return true;
  // Alembic: <hex>_description.sql
  if (/^[0-9a-f]{12}_/.test(filename)) return true;
  // Generic: any .sql file
  return true;
}

/**
 * Walk a directory recursively, collecting .sql files.
 * Skips node_modules, .git, build output directories.
 */
function walkDir(dir: string, results: string[]): void {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return; // Permission error or not a directory
  }

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      // Skip non-source directories
      if (
        entry.name === "node_modules" ||
        entry.name === ".git" ||
        entry.name === "target" ||
        entry.name === "build" ||
        entry.name === "dist" ||
        entry.name === "__pycache__" ||
        entry.name.startsWith(".")
      ) {
        continue;
      }
      walkDir(fullPath, results);
    } else if (entry.isFile() && isMigrationFilename(entry.name)) {
      results.push(fullPath);
    }
  }
}

/**
 * Returns true if the file content contains at least one CREATE TABLE or
 * ALTER TABLE statement — i.e., it is a DDL file, not a pure data/query script.
 */
export function isDdlFile(content: string): boolean {
  return /CREATE\s+(?:UNLOGGED\s+)?TABLE\s+/i.test(content) ||
    /ALTER\s+TABLE\s+/i.test(content);
}

/**
 * Find all SQL migration/DDL files in a repo.
 *
 * Strategy:
 * 1. Check all known migration directories and collect .sql files from them.
 * 2. For any remaining .sql files found during generic walk, filter by isDdlFile.
 * 3. Deduplicate and return sorted list of absolute paths.
 */
export function findSqlFiles(repoRoot: string): string[] {
  const found = new Set<string>();

  // Phase 1: Scan known migration directories (high confidence)
  for (const relDir of KNOWN_MIGRATION_DIRS) {
    const absDir = path.join(repoRoot, relDir);
    if (!fs.existsSync(absDir)) continue;

    const stat = fs.statSync(absDir);
    if (stat.isDirectory()) {
      walkDir(absDir, []).forEach((f) => found.add(f));
      const files: string[] = [];
      walkDir(absDir, files);
      files.forEach((f) => found.add(f));
    } else if (stat.isFile() && absDir.endsWith(".sql")) {
      // Handle "db/structure.sql" as a direct file path
      found.add(absDir);
    }
  }

  // Phase 2: Generic walk — find any .sql files not already captured,
  // filtered by isDdlFile content check
  const allSql: string[] = [];
  walkDir(repoRoot, allSql);

  for (const filePath of allSql) {
    if (found.has(filePath)) continue;
    try {
      const content = fs.readFileSync(filePath, "utf8");
      if (isDdlFile(content)) {
        found.add(filePath);
      }
    } catch {
      // Skip unreadable files
    }
  }

  return Array.from(found).sort();
}
