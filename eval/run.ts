#!/usr/bin/env bun
/**
 * Company Brain eval harness runner.
 *
 * Reads eval/evals.yaml, calls the tRPC API for each eval case,
 * checks assertions, and prints a pass/fail summary.
 *
 * Usage:
 *   CB_SCOPE=my-org/my-repo bun run eval/run.ts
 *   CB_SCOPE=test CB_API_URL=http://localhost:8090 bun run eval/run.ts [--filter <id>]
 *
 * Exit code: 0 if all pass, 1 if any fail.
 */
import * as fs   from "node:fs";
import * as path from "node:path";
import * as url  from "node:url";
import yaml from "js-yaml";

// ── Types ────────────────────────────────────────────────────────────────────

interface EvalExpect {
  result_not_null?:       boolean;
  result_null?:           boolean;
  reason?:                string;
  min_confidence?:        number;
  result_contains_key?:   string;
  result_array_min_length?: number;
  result_item_field?:     { field: string; value: string };
}

interface EvalCase {
  id:       string;
  question: string;
  tool:     string;
  input:    Record<string, unknown>;
  expect:   EvalExpect;
}

interface EvalFile {
  version: string;
  scope:   string;
  evals:   EvalCase[];
}

// ── tRPC caller ───────────────────────────────────────────────────────────────

async function callTrpc(
  apiUrl: string,
  procedure: string,
  input: Record<string, unknown>,
): Promise<{ kind?: string; result?: unknown; reason?: string; confidence?: number; [k: string]: unknown }> {
  const url = `${apiUrl}/trpc/${procedure}`;
  const res = await fetch(url + "?input=" + encodeURIComponent(JSON.stringify(input)), {
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} from ${url}`);
  }
  const body = await res.json() as { result?: { data?: unknown } };
  return (body.result?.data ?? body) as Record<string, unknown>;
}

// ── Assertion checker ─────────────────────────────────────────────────────────

interface CheckResult { pass: boolean; failures: string[] }

function checkAssertions(evalCase: EvalCase, data: Record<string, unknown>): CheckResult {
  const failures: string[] = [];
  const { expect } = evalCase;

  const isNull   = data["kind"] === "absent";
  const isSuccess = data["kind"] === "success";
  const result   = data["result"];
  const confidence = typeof data["confidence"] === "number" ? data["confidence"] : undefined;
  const reason   = data["reason"] as string | undefined;

  if (expect.result_not_null) {
    if (isNull || result === null || result === undefined) {
      failures.push(`Expected non-null result but got absent/null`);
    }
  }

  if (expect.result_null) {
    if (isSuccess && result !== null && result !== undefined) {
      failures.push(`Expected null/absent result but got success`);
    }
    if (expect.reason && reason && !reason.includes(expect.reason)) {
      failures.push(`Expected absence reason '${expect.reason}' but got '${reason}'`);
    }
  }

  if (expect.min_confidence !== undefined) {
    if (confidence !== undefined && confidence < expect.min_confidence) {
      failures.push(`Confidence ${confidence.toFixed(2)} < min ${expect.min_confidence}`);
    }
  }

  if (expect.result_contains_key) {
    const key = expect.result_contains_key;
    if (!isSuccess || typeof result !== "object" || result === null || !(key in (result as object))) {
      failures.push(`Expected result to contain key '${key}'`);
    }
  }

  if (expect.result_array_min_length !== undefined) {
    if (!Array.isArray(result) || result.length < expect.result_array_min_length) {
      failures.push(`Expected array of ≥${expect.result_array_min_length} items, got ${Array.isArray(result) ? result.length : "non-array"}`);
    }
  }

  if (expect.result_item_field) {
    const { field, value } = expect.result_item_field;
    if (!Array.isArray(result) || !result.some(
      (item: unknown) => typeof item === "object" && item !== null && (item as Record<string, unknown>)[field] === value
    )) {
      failures.push(`Expected at least one result item with ${field}=${value}`);
    }
  }

  return { pass: failures.length === 0, failures };
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
  const evalsPath = path.join(__dirname, "evals.yaml");

  if (!fs.existsSync(evalsPath)) {
    console.error(`Eval file not found: ${evalsPath}`);
    process.exit(1);
  }

  const evalFile = yaml.load(fs.readFileSync(evalsPath, "utf8")) as EvalFile;

  const apiUrl   = process.env["CB_API_URL"]  ?? "http://localhost:8090";
  const scopeEnv = process.env["CB_SCOPE"]    ?? "";
  const filter   = process.argv.includes("--filter")
    ? process.argv[process.argv.indexOf("--filter") + 1]
    : undefined;

  // Resolve scope template
  const scope = scopeEnv || evalFile.scope.replace(/\{\{.*?\}\}/, "test-scope");

  let evals = evalFile.evals;
  if (filter) evals = evals.filter(e => e.id === filter || e.id.includes(filter));

  console.log(`\nCompany Brain Eval Harness`);
  console.log(`  API:    ${apiUrl}`);
  console.log(`  Scope:  ${scope}`);
  console.log(`  Cases:  ${evals.length}\n`);

  const results: Array<{ id: string; pass: boolean; durationMs: number; failures: string[] }> = [];

  for (const evalCase of evals) {
    const resolvedInput = JSON.parse(
      JSON.stringify(evalCase.input)
        .replace(/\{\{scope\}\}/g, scope)
        .replace(/\{\{CB_SCOPE\}\}/g, scope)
    ) as Record<string, unknown>;

    const start = Date.now();
    let data: Record<string, unknown>;

    try {
      data = await callTrpc(apiUrl, evalCase.tool, resolvedInput);
    } catch (err) {
      const durationMs = Date.now() - start;
      console.log(`  ✗ ${evalCase.id.padEnd(40)} ${durationMs}ms  ERROR: ${err}`);
      results.push({ id: evalCase.id, pass: false, durationMs, failures: [String(err)] });
      continue;
    }

    const { pass, failures } = checkAssertions(evalCase, data);
    const durationMs = Date.now() - start;
    const icon = pass ? "✓" : "✗";
    console.log(`  ${icon} ${evalCase.id.padEnd(40)} ${durationMs}ms`);
    if (!pass) {
      for (const f of failures) console.log(`      → ${f}`);
    }
    results.push({ id: evalCase.id, pass, durationMs, failures });
  }

  const passed  = results.filter(r => r.pass).length;
  const failed  = results.filter(r => !r.pass).length;
  const totalMs = results.reduce((s, r) => s + r.durationMs, 0);

  console.log(`\nResults: ${passed}/${results.length} passed (${failed} failed) in ${totalMs}ms`);

  if (failed > 0) {
    console.log("\nFailed cases:");
    for (const r of results.filter(r => !r.pass)) {
      console.log(`  ${r.id}`);
      for (const f of r.failures) console.log(`    → ${f}`);
    }
    process.exit(1);
  }
}

main().catch(err => { console.error(err); process.exit(1); });
