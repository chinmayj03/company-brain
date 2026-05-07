/**
 * File scanner for Python ORM model files (SQLAlchemy and Django).
 *
 * Finds Python files that likely contain ORM model definitions.
 */

import { readdirSync, statSync, readFileSync, existsSync } from "fs";
import { join, basename, dirname } from "path";

const SKIP_DIRS = new Set([
  "node_modules", ".git", "__pycache__", ".tox", ".eggs",
  "dist", "build", ".venv", "venv", "env", ".env", "site-packages",
  "migrations", // Django migration files — not model files
]);

const MODEL_CONTENT_SIGNALS = [
  "Column(",
  "db.Column(",
  "mapped_column(",
  "models.CharField",
  "models.IntegerField",
  "models.Model",
  "db.Model",
];

function walkPythonFiles(dir: string, results: string[]): void {
  if (!existsSync(dir)) return;
  try {
    for (const entry of readdirSync(dir)) {
      if (SKIP_DIRS.has(entry) || entry.startsWith(".")) continue;
      const fullPath = join(dir, entry);
      let stat;
      try {
        stat = statSync(fullPath);
      } catch {
        continue;
      }
      if (stat.isDirectory()) {
        walkPythonFiles(fullPath, results);
      } else if (entry.endsWith(".py")) {
        results.push(fullPath);
      }
    }
  } catch {
    // ignore permission errors
  }
}

/** Check if an absolute file path is a candidate model file by name heuristics */
function isModelFileByName(filePath: string): boolean {
  const name = basename(filePath);
  const dir = dirname(filePath);
  const dirName = basename(dir);

  // models.py anywhere
  if (name === "models.py") return true;

  // files inside a models/ directory
  if (dirName === "models") return true;

  // db/models.py or database/models.py
  if ((dirName === "db" || dirName === "database") && name === "models.py") return true;

  return false;
}

export function findPythonModelFiles(repoRoot: string): string[] {
  const allPyFiles: string[] = [];
  walkPythonFiles(repoRoot, allPyFiles);

  return allPyFiles.filter((filePath) => {
    // Name-based heuristic first (fast path)
    if (!isModelFileByName(filePath)) return false;

    // Content signal check
    try {
      const content = readFileSync(filePath, "utf8");
      return MODEL_CONTENT_SIGNALS.some((sig) => content.includes(sig));
    } catch {
      return false;
    }
  });
}
