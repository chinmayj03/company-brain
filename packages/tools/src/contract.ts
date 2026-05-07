import { z } from "zod";

/** Successful result with a value */
export interface ToolSuccess<T> {
  kind: "success";
  result: T;
  confidence: number;
  source: string;
}

export type AbsenceReason = "no_match" | "graph_empty" | "scope_not_found" | "error";

/** Absent result with an explanation */
export interface ToolAbsence {
  kind: "absent";
  reason: AbsenceReason;
  message?: string;
}

export type ToolResult<T> = ToolSuccess<T> | ToolAbsence;

export function success<T>(result: T, confidence: number, source = "neo4j"): ToolSuccess<T> {
  return { kind: "success", result, confidence, source };
}

export function absent(reason: AbsenceReason, message?: string): ToolAbsence {
  return { kind: "absent", reason, message };
}

export const toolResultSchema = <T extends z.ZodType>(resultSchema: T) =>
  z.discriminatedUnion("kind", [
    z.object({ kind: z.literal("success"), result: resultSchema, confidence: z.number(), source: z.string() }),
    z.object({ kind: z.literal("absent"), reason: z.enum(["no_match","graph_empty","scope_not_found","error"]), message: z.string().optional() }),
  ]);
