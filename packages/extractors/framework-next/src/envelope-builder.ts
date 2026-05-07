/**
 * Builds NodeEnvelope and EdgeEnvelope instances from the raw Next.js extraction result.
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { NextExtractionResult } from "./types.js";

export interface NextWriteBatch {
  nodes: NodeEnvelope[];
  edges: EdgeEnvelope[];
}

function md5(input: string): string {
  return crypto.createHash("md5").update(input).digest("hex");
}

function node(
  id: string,
  type: NodeType,
  name: string,
  scope: string,
  commitSha: string,
  extractor: ExtractorRef,
  now: string,
  confidence: number,
  derivation: "framework_parser" | "static_analysis",
  attributes: Record<string, unknown>,
  qualifiedName?: string,
): NodeEnvelope {
  return {
    id,
    type,
    name,
    ...(qualifiedName ? { qualified_name: qualifiedName } : {}),
    source_uri: `urn:cb:next:${scope}`,
    source_checksum: md5(id),
    extractor,
    extraction_timestamp: now,
    confidence,
    derivation,
    created_at_commit: commitSha,
    last_modified_commit: commitSha,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    status: "active",
    attributes,
  };
}

function edge(
  fromId: string,
  type: EdgeType,
  toId: string,
  scope: string,
  commitSha: string,
  extractor: ExtractorRef,
  cardinality: "1-1" | "1-n" | "n-1" | "n-n",
  derivation: "framework_parser" | "static_analysis" = "framework_parser",
): EdgeEnvelope {
  return {
    id: `${fromId}>>${type}>>${toId}`,
    type,
    source_id: fromId,
    target_id: toId,
    cardinality,
    source_uri: `urn:cb:next:${scope}`,
    extractor,
    derivation,
    confidence: derivation === "framework_parser" ? 0.95 : 0.85,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: {},
  };
}

function screenUrn(scope: string, urlPattern: string): string {
  const base = urlPattern.replace(/^\//, "") || "_root";
  return buildUrn({ source: "next", scope, artifact: `screens/${base}` });
}

function routeUrn(scope: string, urlPattern: string): string {
  const base = urlPattern.replace(/^\//, "") || "_root";
  return buildUrn({ source: "next", scope, artifact: `route-nodes/${base}` });
}

function apiRouteUrn(scope: string, urlPattern: string): string {
  const base = urlPattern.replace(/^\//, "") || "_root";
  return buildUrn({ source: "next", scope, artifact: `routes/${base}` });
}

function layoutUrn(scope: string, urlPattern: string): string {
  const base = urlPattern.replace(/^\//, "") || "_root";
  return buildUrn({ source: "next", scope, artifact: `layouts/${base}` });
}

function componentUrn(scope: string, filePath: string): string {
  return buildUrn({ source: "next", scope, artifact: `components/${filePath}` });
}

export function buildNextEnvelopes(
  result: NextExtractionResult,
  scope: string,
  commitSha: string,
  extractorVersion: string,
): NextWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-next", version: extractorVersion };
  const now = new Date().toISOString();

  // ── Screens ──────────────────────────────────────────────────────────────
  for (const screen of result.screens) {
    const id = screenUrn(scope, screen.urlPattern);
    nodes.push(node(id, "Screen", screen.urlPattern, scope, commitSha, extractor, now, 0.95, "framework_parser", {
      url_pattern: screen.urlPattern,
      ssr: screen.isSSR,
      ssg: screen.isSSG,
      dynamic_segments: screen.dynamicSegments,
      router_type: screen.routerType,
      file_path: screen.filePath,
    }, `${scope}:${screen.urlPattern}`));

    const routeId = routeUrn(scope, screen.urlPattern);
    nodes.push(node(routeId, "Route", screen.urlPattern, scope, commitSha, extractor, now, 0.95, "framework_parser", {
      url_pattern: screen.urlPattern,
      dynamic_segments: screen.dynamicSegments,
    }, `${scope}:route:${screen.urlPattern}`));

    edges.push(edge(routeId, "routes_to", id, scope, commitSha, extractor, "n-1"));
  }

  // ── API Routes ────────────────────────────────────────────────────────────
  for (const apiRoute of result.apiRoutes) {
    const id = apiRouteUrn(scope, apiRoute.urlPattern);
    nodes.push(node(id, "APIRoute", apiRoute.urlPattern, scope, commitSha, extractor, now, 0.95, "framework_parser", {
      http_methods: apiRoute.httpMethods,
      path_pattern: apiRoute.urlPattern,
      dynamic_segments: apiRoute.dynamicSegments,
      is_catch_all: apiRoute.isCatchAll,
      router_type: apiRoute.routerType,
      file_path: apiRoute.filePath,
    }, `${scope}:api:${apiRoute.urlPattern}`));
  }

  // ── Layouts ───────────────────────────────────────────────────────────────
  for (const layout of result.layouts) {
    const id = layoutUrn(scope, layout.urlPattern);
    nodes.push(node(id, "Layout", layout.urlPattern, scope, commitSha, extractor, now, 0.95, "framework_parser", {
      path_pattern: layout.urlPattern,
      is_root: layout.isRoot,
      file_path: layout.filePath,
    }, `${scope}:layout:${layout.urlPattern}`));

    if (layout.parentPattern !== null) {
      const parentId = layoutUrn(scope, layout.parentPattern);
      edges.push(edge(id, "child_of", parentId, scope, commitSha, extractor, "n-1"));
    }
  }

  // ── child_of edges for screens → nearest layout ───────────────────────────
  for (const screen of result.screens) {
    const screenId = screenUrn(scope, screen.urlPattern);
    const parts = screen.urlPattern.split("/").filter(Boolean);
    for (let i = parts.length; i >= 0; i--) {
      const candidate = i === 0 ? "/" : "/" + parts.slice(0, i).join("/");
      if (result.layouts.some((l) => l.urlPattern === candidate)) {
        const layoutId = layoutUrn(scope, candidate);
        edges.push(edge(screenId, "child_of", layoutId, scope, commitSha, extractor, "n-1"));
        break;
      }
    }
  }

  // ── Components ────────────────────────────────────────────────────────────
  for (const comp of result.components) {
    const id = componentUrn(scope, comp.filePath);
    nodes.push(node(id, "Component", comp.name, scope, commitSha, extractor, now, 0.85, "static_analysis", {
      is_server_component: comp.isServerComponent,
      is_client_component: comp.isClientComponent,
      exported: comp.exported,
      file_path: comp.filePath,
    }, `${scope}:component:${comp.filePath}`));
  }

  return { nodes, edges };
}
