/**
 * Path normalization utilities for Next.js route segments.
 *
 * Handles both App Router (app/) and Pages Router (pages/) conventions.
 */

import { basename, dirname, relative } from "path";

/**
 * Filenames that are NOT routes — they are colocated files in app/.
 */
const NON_ROUTE_FILENAMES = new Set([
  "loading",
  "error",
  "not-found",
  "global-error",
  "template",
  "default",
  "_app",
  "_document",
  "_error",
]);

/**
 * Filenames that identify layout files in App Router.
 */
const LAYOUT_FILENAMES = new Set(["layout"]);

/**
 * Filenames that identify page files.
 */
const PAGE_FILENAMES = new Set(["page", "index"]);

/**
 * Filenames that identify API route handlers in App Router.
 */
const ROUTE_FILENAMES = new Set(["route"]);

/**
 * TypeScript/JavaScript file extensions that are valid entry points.
 */
const VALID_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs"]);

export type FileRole = "page" | "api-route" | "layout" | "component" | "non-route";

/**
 * Determine the role of a file within a Next.js project.
 */
export function classifyFile(
  repoRelativePath: string,
  rootDir: string,
): { role: FileRole; routerType: "app" | "pages" | null } {
  const ext = repoRelativePath.slice(repoRelativePath.lastIndexOf("."));
  if (!VALID_EXTENSIONS.has(ext)) return { role: "non-route", routerType: null };

  const stem = basename(repoRelativePath, ext);

  // App Router: anything under app/
  if (repoRelativePath.startsWith("app/") || repoRelativePath.includes("/app/")) {
    const appRelative = repoRelativePath.replace(/^.*?(?:^|\/)app\//, "");
    if (LAYOUT_FILENAMES.has(stem)) return { role: "layout", routerType: "app" };
    if (PAGE_FILENAMES.has(stem)) return { role: "page", routerType: "app" };
    if (ROUTE_FILENAMES.has(stem)) return { role: "api-route", routerType: "app" };
    if (NON_ROUTE_FILENAMES.has(stem)) return { role: "non-route", routerType: "app" };
    // Anything else in app/ is a colocated component
    return { role: "component", routerType: "app" };
  }

  // Pages Router: anything under pages/
  if (repoRelativePath.startsWith("pages/") || repoRelativePath.includes("/pages/")) {
    if (NON_ROUTE_FILENAMES.has(stem)) return { role: "non-route", routerType: "pages" };
    // pages/api/** are API routes
    if (repoRelativePath.match(/(?:^|\/)pages\/api\//)) return { role: "api-route", routerType: "pages" };
    // Everything else is a page
    return { role: "page", routerType: "pages" };
  }

  // Components directory
  if (
    repoRelativePath.startsWith("components/") ||
    repoRelativePath.startsWith("src/components/")
  ) {
    return { role: "component", routerType: null };
  }

  return { role: "non-route", routerType: null };
}

/**
 * Normalize a filesystem path to a Next.js URL pattern.
 *
 * Input examples:
 *   app/billing/[invoiceId]/page.tsx  → /billing/[invoiceId]
 *   pages/billing/[invoiceId].tsx     → /billing/[invoiceId]
 *   app/layout.tsx                    → /  (root layout)
 *   pages/api/users/[id].ts           → /api/users/[id]
 */
export function normalizeToUrlPattern(repoRelativePath: string, role: FileRole): string {
  const ext = repoRelativePath.slice(repoRelativePath.lastIndexOf("."));
  const stem = basename(repoRelativePath, ext);

  // Strip app/ prefix and route group segments like (auth)
  let path = repoRelativePath
    .replace(/^.*?(?:^|\/)(?:app|pages)\//, "/")
    .replace(/\([^)]+\)\//g, ""); // strip route groups (auth)/, (marketing)/

  // Remove trailing filename if it's a reserved name
  const terminalNames = new Set([
    "page", "index", "route", "layout", "loading",
    "error", "not-found", "global-error", "template", "default",
  ]);
  const pathStem = basename(path, ext);
  if (terminalNames.has(pathStem)) {
    path = dirname(path);
    if (path === ".") path = "/";
  } else {
    // Strip extension only
    path = path.slice(0, path.length - ext.length);
  }

  // Normalise to absolute path
  if (!path.startsWith("/")) path = "/" + path;
  // Clean double slashes
  path = path.replace(/\/+/g, "/");
  // Normalize root
  if (path === "" || path === ".") path = "/";

  return path;
}

/**
 * Extract dynamic segment names from a URL pattern.
 * /billing/[invoiceId]/items/[itemId] → ["invoiceId", "itemId"]
 * /[...slug] → ["slug"]
 */
export function extractDynamicSegments(urlPattern: string): string[] {
  const matches = urlPattern.match(/\[(?:\.\.\.)?([^\]]+)\]/g) ?? [];
  return matches.map((m) => m.replace(/\[\.\.\./, "").replace(/\[/, "").replace(/\]/, ""));
}

/**
 * Whether a URL pattern contains a catch-all segment [...slug].
 */
export function isCatchAll(urlPattern: string): boolean {
  return /\[\.\.\./.test(urlPattern);
}

/**
 * Infer the parent layout pattern for a given URL pattern.
 * /billing/[invoiceId] → /billing → /
 * Returns null if there is no parent (already at root).
 */
export function parentLayoutPattern(urlPattern: string): string | null {
  if (urlPattern === "/") return null;
  const parts = urlPattern.split("/").filter(Boolean);
  if (parts.length <= 1) return "/";
  return "/" + parts.slice(0, -1).join("/");
}
