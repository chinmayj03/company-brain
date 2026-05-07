/**
 * OpenAPI / Swagger parser.
 *
 * Supports OpenAPI 3.0.x, 3.1.x, and Swagger 2.0.
 * Parses YAML and JSON formats.
 *
 * v1 limitations (intentional):
 * - $ref resolution is shallow (records the reference string but doesn't follow it)
 * - allOf/anyOf/oneOf treated as opaque schema objects
 * - No circular reference handling needed (we just store schema_json)
 */

import yaml from "js-yaml";
import type { ExtractedOpenApiDoc, ExtractedOperation, OpenApiParameter, OpenApiMediaType } from "./types.js";

type AnyObject = Record<string, unknown>;

function isObject(v: unknown): v is AnyObject {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function safeStr(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function safeArr<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function parseBool(v: unknown): boolean {
  return v === true;
}

/** Generate a stable operationId when the spec doesn't provide one */
function syntheticOperationId(method: string, path: string): string {
  const sanitizedPath = path
    .replace(/^\//, "")
    .replace(/\//g, "_")
    .replace(/[{}]/g, "")
    .replace(/-/g, "_");
  return `${method.toLowerCase()}_${sanitizedPath}`;
}

/** Parse parameters from a list */
function parseParameters(params: unknown[]): OpenApiParameter[] {
  return params
    .filter(isObject)
    .map((p) => ({
      name: safeStr(p.name) ?? "",
      in: (safeStr(p.in) as OpenApiParameter["in"]) ?? "query",
      required: parseBool(p.required),
      schema: p.schema ?? {},
    }))
    .filter((p) => p.name);
}

/** Parse media type map from an OpenAPI 3.x request/response body */
function parseMediaTypeMap(content: unknown): OpenApiMediaType[] {
  if (!isObject(content)) return [];
  return Object.entries(content).map(([contentType, mediaObj]) => ({
    contentType,
    schema: isObject(mediaObj) ? (mediaObj as AnyObject).schema ?? {} : {},
  }));
}

/** Parse a Swagger 2.0 body parameter as a single media type */
function parseSwagger2Body(parameters: unknown[]): OpenApiMediaType | null {
  const bodyParam = parameters.filter(isObject).find((p) => p.in === "body");
  if (!bodyParam) return null;
  return {
    contentType: "application/json",
    schema: (bodyParam as AnyObject).schema ?? {},
  };
}

/** Parse OpenAPI 3.x paths object */
function parseOpenApi3Paths(paths: AnyObject): ExtractedOperation[] {
  const operations: ExtractedOperation[] = [];

  for (const [path, pathItem] of Object.entries(paths)) {
    if (!isObject(pathItem)) continue;
    const pathLevelParams = safeArr<unknown>(pathItem.parameters);

    const HTTP_METHODS = ["get", "post", "put", "delete", "patch", "head", "options", "trace"];
    for (const method of HTTP_METHODS) {
      const op = pathItem[method];
      if (!isObject(op)) continue;

      const opParams = safeArr<unknown>(op.parameters);
      const allParams = parseParameters([...pathLevelParams, ...opParams]);

      // Request body
      let requestBody: OpenApiMediaType | null = null;
      if (isObject(op.requestBody)) {
        const content = (op.requestBody as AnyObject).content;
        const mediaTypes = parseMediaTypeMap(content);
        requestBody = mediaTypes[0] ?? null; // primary content type
      }

      // Responses
      const responses: ExtractedOperation["responses"] = [];
      if (isObject(op.responses)) {
        for (const [statusCode, responseObj] of Object.entries(op.responses as AnyObject)) {
          if (!isObject(responseObj)) continue;
          const description = safeStr((responseObj as AnyObject).description);
          const mediaTypes = parseMediaTypeMap((responseObj as AnyObject).content);
          responses.push({ statusCode, description, mediaTypes });
        }
      }

      const operationId =
        safeStr(op.operationId) ?? syntheticOperationId(method.toUpperCase(), path);

      operations.push({
        operationId,
        path,
        method: method.toUpperCase(),
        summary: safeStr(op.summary),
        description: safeStr(op.description),
        tags: safeArr<string>(op.tags),
        deprecated: parseBool(op.deprecated),
        parameters: allParams,
        requestBody,
        responses,
      });
    }
  }

  return operations;
}

/** Parse Swagger 2.0 paths object */
function parseSwagger2Paths(paths: AnyObject): ExtractedOperation[] {
  const operations: ExtractedOperation[] = [];

  for (const [path, pathItem] of Object.entries(paths)) {
    if (!isObject(pathItem)) continue;
    const pathLevelParams = safeArr<unknown>(pathItem.parameters);

    const HTTP_METHODS = ["get", "post", "put", "delete", "patch", "head", "options"];
    for (const method of HTTP_METHODS) {
      const op = pathItem[method];
      if (!isObject(op)) continue;

      const opParams = safeArr<unknown>(op.parameters);
      const allParams = [...pathLevelParams, ...opParams];
      const nonBodyParams = parseParameters(allParams.filter((p) => isObject(p) && (p as AnyObject).in !== "body"));
      const requestBody = parseSwagger2Body(allParams);

      const responses: ExtractedOperation["responses"] = [];
      if (isObject(op.responses)) {
        for (const [statusCode, responseObj] of Object.entries(op.responses as AnyObject)) {
          if (!isObject(responseObj)) continue;
          const description = safeStr((responseObj as AnyObject).description);
          const schema = (responseObj as AnyObject).schema;
          const mediaTypes: OpenApiMediaType[] = schema
            ? [{ contentType: "application/json", schema }]
            : [];
          responses.push({ statusCode, description, mediaTypes });
        }
      }

      const operationId =
        safeStr(op.operationId) ?? syntheticOperationId(method.toUpperCase(), path);

      operations.push({
        operationId,
        path,
        method: method.toUpperCase(),
        summary: safeStr(op.summary),
        description: safeStr(op.description),
        tags: safeArr<string>(op.tags),
        deprecated: parseBool(op.deprecated),
        parameters: nonBodyParams,
        requestBody,
        responses,
      });
    }
  }

  return operations;
}

/**
 * Parse an OpenAPI/Swagger document from YAML or JSON content.
 */
export function parseOpenApiDoc(content: string, filePath: string): ExtractedOpenApiDoc {
  const filenameStem = filePath.split("/").pop()!.replace(/\.(yaml|yml|json)$/, "");

  // Parse YAML/JSON
  let doc: unknown;
  try {
    doc = yaml.load(content);
  } catch {
    try {
      doc = JSON.parse(content);
    } catch {
      throw new Error(`Failed to parse ${filePath} as YAML or JSON`);
    }
  }

  if (!isObject(doc)) {
    throw new Error(`${filePath} does not contain an object at root`);
  }

  // Determine format and version
  const openApiVersion = safeStr(doc.openapi);
  const swaggerVersion = safeStr(doc.swagger);

  let format: "openapi" | "swagger";
  let specVersion: string | null;

  if (openApiVersion) {
    format = "openapi";
    specVersion = openApiVersion;
  } else if (swaggerVersion) {
    format = "swagger";
    specVersion = swaggerVersion;
  } else {
    throw new Error(`${filePath} is not a valid OpenAPI or Swagger document`);
  }

  // Info
  const info = isObject(doc.info) ? (doc.info as AnyObject) : {};
  const title = safeStr(info.title);

  // Paths
  const paths = isObject(doc.paths) ? (doc.paths as AnyObject) : {};
  const operations =
    format === "openapi" ? parseOpenApi3Paths(paths) : parseSwagger2Paths(paths);

  return {
    filePath,
    filenameStem,
    format,
    specVersion,
    title,
    operations,
  };
}
