#!/usr/bin/env node
// Headless smoke test for the brain MCP wire format.
//
// Runs WITHOUT VS Code so the acceptance test can spin it up against a live
// MCP server (built via build_server() in companybrain.harness.mcp_server).
//
// Inputs (env):
//   MCP_URL   — base URL of the brain MCP server (default http://localhost:8765)
//   QUESTION  — the natural-language question to send (required)
//
// Output: the parsed `query_brain` result on stdout, JSON-encoded. Exits non-zero
// on any error so the caller can assert on the return code.

const MCP_URL = (process.env.MCP_URL || 'http://localhost:8765').replace(/\/$/, '');
const QUESTION = process.env.QUESTION;

if (!QUESTION) {
  console.error('headless-query: QUESTION env var is required');
  process.exit(2);
}

async function rpc(method, params) {
  const resp = await fetch(`${MCP_URL}/mcp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

(async () => {
  try {
    const callResp = await rpc('tools/call', {
      name: 'query_brain',
      arguments: { question: QUESTION },
    });
    if (callResp.error) {
      console.error(`MCP error: ${JSON.stringify(callResp.error)}`);
      process.exit(1);
    }
    const result = callResp.result || {};
    if (result.isError) {
      console.error(`Tool error: ${JSON.stringify(result.content)}`);
      process.exit(1);
    }
    let payload = result.structuredContent;
    if (payload === undefined && Array.isArray(result.content) && result.content[0]) {
      payload = JSON.parse(result.content[0].text);
    }
    if (!payload || typeof payload !== 'object') {
      throw new Error('no parseable payload in MCP response');
    }
    // Always emit a `summary_md` field so the test (and any downstream
    // tooling) can pivot on a single key regardless of match count.
    const matches = Array.isArray(payload.matches) ? payload.matches : [];
    const summary_md =
      matches.length === 0
        ? `_No matches for "${QUESTION}"._`
        : matches
            .slice(0, 5)
            .map((m) => `- **${m.qualified_name}** — ${m.summary || '(no summary)'}`)
            .join('\n');
    process.stdout.write(JSON.stringify({ summary_md, ...payload }) + '\n');
    process.exit(0);
  } catch (err) {
    console.error(`headless-query: ${err.message || err}`);
    process.exit(1);
  }
})();
