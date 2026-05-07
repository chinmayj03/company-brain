/**
 * One-shot Neo4j migration to canonical URN per ADR-0013.
 *
 * Reads existing nodes whose `id` matches the legacy 'urn:cb:llm:...' prefix
 * and rewrites them to the canonical format:
 *   'urn:cb:{tenant}:code:{repo}:{entity_type}:{qname}'
 *
 * Run with:
 *   bun run apps/api/src/scripts/migrate-urn.ts [tenant] [repo]
 *
 * Idempotent: nodes whose `id` already starts with the new prefix are skipped.
 * The original id is preserved in `legacy_id` so the rollback Cypher can restore it.
 *
 * Arguments:
 *   tenant  — workspace slug (default: 'dev')
 *   repo    — repository slug (default: 'monorepo')
 */

import neo4j from "neo4j-driver";

const [, , tenantArg, repoArg] = process.argv;
const tenant = tenantArg ?? "dev";
const repo = repoArg ?? "monorepo";

const uri = process.env["NEO4J_URI"] ?? "bolt://localhost:7687";
const user = process.env["NEO4J_USER"] ?? "neo4j";
const password = process.env["NEO4J_PASSWORD"] ?? "password";

const driver = neo4j.driver(uri, neo4j.auth.basic(user, password));

async function migrate(): Promise<void> {
  const session = driver.session();
  const newPrefix = `urn:cb:${tenant}:code:${repo}`;

  try {
    // Rewrite all nodes that still carry the legacy 'urn:cb:llm:' prefix.
    // The new id format: urn:cb:{tenant}:code:{repo}:{entity_type}:{qname}
    // where entity_type defaults to the node's existing `entity_type` property
    // (set by the TypeScript extractors) or falls back to 'component'.
    //
    // The last segment of the legacy URN is used as the qualified_name.
    // e.g. 'urn:cb:llm:dev:src/Foo.ts:Foo' → 'urn:cb:dev:code:monorepo:component:Foo'
    const result = await session.run(
      `MATCH (n)
       WHERE n.id STARTS WITH 'urn:cb:llm:'
         AND NOT n.id STARTS WITH $newPrefix
       WITH n,
            split(n.id, ':') AS parts
       WITH n,
            parts[size(parts) - 1] AS qname,
            coalesce(n.entity_type, 'component') AS etype
       SET n.legacy_id = n.id,
           n.id        = $newPrefix + ':' + etype + ':' + qname
       RETURN count(n) AS migrated`,
      { newPrefix }
    );

    const migrated = result.records[0]?.get("migrated") ?? 0;
    console.log(
      `URN migration complete  tenant=${tenant}  repo=${repo}  migrated=${migrated}`
    );

    // Report how many nodes were already in the new format (idempotency check).
    const alreadyResult = await session.run(
      `MATCH (n) WHERE n.id STARTS WITH $newPrefix RETURN count(n) AS already`,
      { newPrefix }
    );
    const already = alreadyResult.records[0]?.get("already") ?? 0;
    console.log(`Nodes already in canonical format: ${already}`);
  } finally {
    await session.close();
  }
}

migrate()
  .catch((err) => {
    console.error("Migration failed:", err);
    process.exit(1);
  })
  .finally(() => driver.close());
