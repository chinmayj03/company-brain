/**
 * File scanner for Java @Entity files.
 *
 * Recursively searches src/main/java/ and src/ for *.java files
 * that contain the @Entity annotation.
 */

import { readdirSync, statSync, readFileSync, existsSync } from "fs";
import { join } from "path";

const SKIP_DIRS = new Set(["node_modules", ".git", "target", "build", ".gradle", ".mvn", "out"]);

function walkJavaFiles(dir: string, results: string[]): void {
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
        walkJavaFiles(fullPath, results);
      } else if (entry.endsWith(".java")) {
        results.push(fullPath);
      }
    }
  } catch {
    // ignore permission errors
  }
}

export function findJavaEntityFiles(repoRoot: string): string[] {
  const candidates: string[] = [];

  // Prefer well-known Maven/Gradle source roots
  const primaryRoots = [
    join(repoRoot, "src", "main", "java"),
    join(repoRoot, "src"),
  ];

  const visited = new Set<string>();

  for (const root of primaryRoots) {
    if (visited.has(root)) continue;
    visited.add(root);
    walkJavaFiles(root, candidates);
  }

  // If no files found via known roots, fall back to full repo scan
  if (candidates.length === 0) {
    walkJavaFiles(repoRoot, candidates);
  }

  // Filter: file must contain @Entity
  return candidates.filter((filePath) => {
    try {
      const content = readFileSync(filePath, "utf8");
      return content.includes("@Entity");
    } catch {
      return false;
    }
  });
}
