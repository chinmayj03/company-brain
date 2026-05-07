/**
 * Internal types for the OpenAPI extractor.
 */

export interface OpenApiParameter {
  name: string;
  in: "path" | "query" | "header" | "cookie";
  required: boolean;
  schema: unknown;
}

export interface OpenApiMediaType {
  contentType: string;
  schema: unknown; // raw JSON Schema object
}

export interface ExtractedOperation {
  operationId: string;
  path: string;
  method: string; // GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
  summary: string | null;
  description: string | null;
  tags: string[];
  deprecated: boolean;
  parameters: OpenApiParameter[];
  requestBody: OpenApiMediaType | null;
  responses: Array<{
    statusCode: string;
    description: string | null;
    mediaTypes: OpenApiMediaType[];
  }>;
}

export interface ExtractedOpenApiDoc {
  filePath: string;
  filenameStem: string;
  format: "openapi" | "swagger";
  specVersion: string | null;
  title: string | null;
  operations: ExtractedOperation[];
}
