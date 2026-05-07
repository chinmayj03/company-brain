/**
 * Scans a repository for Next.js files and classifies them.
 * Returns the raw extraction result before envelope construction.
 */

import { readdirSync, statSync, existsSync, readFileSync } from "fs";
import { join, relative } from "path";
import {
  classifyFile,
  normalizeToUrlPattern,
  extractDynamicSegments,
  isCatchAll,
  parentLayoutPattern,
  type FileRole,
} from "./path-utils.js";
import type {
  NextExtractionResult,
  ExtractedScreen,
  ExtractedAPIRoute,
  ExtractedLayout,
  ExtractedComponent,
  RouterType,
} from "./types.js";

const HTTP_METHOD_EXPORTS = new Set(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]);

/**
 * Detect which HTTP methods are exported from a route file.
 * Uses a simple regex scan — no AST required for top-level exports.
 */
function detectHttpMethods(filePath: string): string[] {
  try {
    const content = readFileSync(filePath, "utf8");
    const methods: string[] = [];
    for (const method of HTTP_METHOD_EXPORTS) {
      // Match: export async function GET, export function GET, export const GET =
      if (new RegExp(`export\\s+(?:async\\s+)?(?:function|const)\\s+${method}\\b`).test(content)) {
        methods.push(method);
      }
    }
    return methods.length > 0 ? methods : ["GET"]; // default fallback
  } catch {
    return ["GET"];
  }
}

/**
 * Detect whether a file has "use client" directive.
 * Only needs to check the first few lines.
 */
function detectClientComponent(filePath: string): boolean {
  try {
    const content = readFileSync(filePath, "utf8");
    const firstLines = content.slice(0, 500);
    return /['"]use client['"]\s*;?/.test(firstLines);
  } catch {
    return false;
  }
}

/**
 * Detect SSG/SSR markers in a page file.
 */
function detectRenderingStrategy(filePath: string): { isSSG: boolean; isSSR: boolean } {
  try {
    const content = readFileSync(filePath, "utf8");
    const isSSG =
      /export\s+(?:async\s+)?function\s+getStaticProps\b/.test(content) ||
      /export\s+(?:async\s+)?function\s+generateStaticParams\b/.test(content);
    const isSSR = /export\s+(?:async\s+)?function\s+getServerSideProps\b/.test(content);
    return { isSSG, isSSR };
  } catch {
    return { isSSG: false, isSSR: false };
  }
}

/**
 * Detect whether a file has default export (component or page).
 */
function hasDefaultExport(filePath: string): boolean {
  try {
    const content = readFileSync(filePath, "utf8");
    return /export\s+default\b/.test(content);
  } catch {
    return false;
  }
}

/**
 * Walk a directory recursively, returning all file paths.
 */
function walk(dir: string): string[] {
  const results: string[] = [];
  try {
    for (const entry of readdirSync(dir)) {
      if (entry.startsWith(".") || entry === "node_modules") continue;
      const fullPath = join(dir, entry);
      const stat = statSync(fullPath);
      if (stat.isDirectory()) {
        results.push(...walk(fullPath));
      } else {
        results.push(fullPath);
      }
    }
  } catch {
    // Permission errors etc.
  }
  return results;
}

/**
 * Detect whether this repository uses Next.js.
 */
export function detectNextJs(repoRoot: string): boolean {
  // Check package.json
  const pkgPath = join(repoRoot, "package.json");
  if (existsSync(pkgPath)) {
    try {
      const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
      const allDeps = {
        ...pkg.dependencies,
        ...pkg.devDependencies,
        ...pkg.peerDependencies,
      };
      if ("next" in allDeps) return true;
    } catch {
      // ignore
    }
  }

  // Check for next.config.* files
  for (const name of ["next.config.js", "next.config.ts", "next.config.mjs", "next.config.cjs"]) {
    if (existsSync(join(repoRoot, name))) return true;
  }

  return false;
}

/**
 * Scan a repository and return the full Next.js extraction result.
 */
export function scanNextRepo(repoRoot: string): NextExtractionResult {
  const allFiles = walk(repoRoot);
  const result: NextExtractionResult = {
    screens: [],
    apiRoutes: [],
    layouts: [],
    components: [],
  };

  for (const absPath of allFiles) {
    const repoRel = relative(repoRoot, absPath);
    const { role, routerType } = classifyFile(repoRel, repoRoot);

    if (role === "non-route" || routerType === null) {
      // Check if it's a component in components/ dir
      if (role === "component") {
        const isClient = detectClientComponent(absPath);
        const ext = repoRel.slice(repoRel.lastIndexOf("."));
        const stem = repoRel.slice(repoRel.lastIndexOf("/") + 1, repoRel.length - ext.length);
        result.components.push({
          filePath: repoRel,
          isServerComponent: !isClient,
          isClientComponent: isClient,
          exported: hasDefaultExport(absPath),
          name: stem,
        });
      }
      continue;
    }

    const urlPattern = normalizeToUrlPattern(repoRel, role);
    const dynamicSegments = extractDynamicSegments(urlPattern);

    switch (role) {
      case "page": {
        const { isSSG, isSSR } = detectRenderingStrategy(absPath);
        const screen: ExtractedScreen = {
          routerType: routerType as RouterType,
          urlPattern,
          filePath: repoRel,
          dynamicSegments,
          isSSG,
          isSSR,
        };
        result.screens.push(screen);
        break;
      }

      case "api-route": {
        const httpMethods = detectHttpMethods(absPath);
        const apiRoute: ExtractedAPIRoute = {
          routerType: routerType as RouterType,
          urlPattern,
          filePath: repoRel,
          httpMethods,
          dynamicSegments,
          isCatchAll: isCatchAll(urlPattern),
        };
        result.apiRoutes.push(apiRoute);
        break;
      }

      case "layout": {
        const parent = parentLayoutPattern(urlPattern);
        const layout: ExtractedLayout = {
          routerType: routerType as RouterType,
          urlPattern,
          filePath: repoRel,
          isRoot: urlPattern === "/",
          parentPattern: parent,
        };
        result.layouts.push(layout);
        break;
      }

      case "component": {
        const isClient = detectClientComponent(absPath);
        const ext = repoRel.slice(repoRel.lastIndexOf("."));
        const stem = repoRel.slice(repoRel.lastIndexOf("/") + 1, repoRel.length - ext.length);
        result.components.push({
          filePath: repoRel,
          isServerComponent: !isClient,
          isClientComponent: isClient,
          exported: hasDefaultExport(absPath),
          name: stem,
        });
        break;
      }
    }
  }

  return result;
}
