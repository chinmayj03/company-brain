/**
 * packages/extractors/git/src/extractor.ts
 *
 * GitExtractor — populates VCS layer nodes:
 *   Organization, Repository, Branch, Commit, PullRequest
 *
 * Runs on every extraction (fast, idempotent). Does not use dirtySet
 * because git history is append-only — always re-syncs the last N commits
 * on the current branch.
 */

import simpleGit, { type SimpleGit, type LogResult } from "simple-git";
import crypto from "crypto";
import {
  Urn,
  buildUrn,
  type NodeEnvelope,
  type EdgeEnvelope,
} from "@company-brain/schema";
import type { GraphClient } from "@company-brain/graph";

// ── Types matching ADR-0003 interface ────────────────────────────────────────

export interface ExtractorManifest {
  name:        string;
  version:     string;
  description: string;
  handles:     string[];
  emits:       string[];
}

export interface ExtractorContext {
  scope:     string;
  commitSha: string;
  repoRoot:  string;
  dirtySet:  string[];
  graph:     GraphClient;
  log:       { info: (...a: unknown[]) => void; warn: (...a: unknown[]) => void; error: (...a: unknown[]) => void };
}

export interface ExtractorResult {
  nodesWritten:    number;
  edgesWritten:    number;
  nodesInvalidated: number;
  warnings:        string[];
  durationMs:      number;
}

// ── Config ───────────────────────────────────────────────────────────────────

/** How many commits to sync on each run (keeps the graph reasonably sized). */
const DEFAULT_COMMIT_DEPTH = 200;

// ── GitExtractor ─────────────────────────────────────────────────────────────

export class GitExtractor {
  readonly manifest: ExtractorManifest = {
    name:        "git",
    version:     "0.1.0",
    description: "Extracts VCS layer: Organization, Repository, Branch, Commit nodes from git history.",
    handles:     ["git_repo"],
    emits:       ["Organization", "Repository", "Branch", "Commit"],
  };

  async extract(ctx: ExtractorContext): Promise<ExtractorResult> {
    const t0 = Date.now();
    const warnings: string[] = [];
    let nodesWritten = 0;
    let edgesWritten = 0;

    const git: SimpleGit = simpleGit(ctx.repoRoot);
    const extractor = { name: this.manifest.name, version: this.manifest.version };
    const now = new Date().toISOString();

    // ── Parse scope → orgSlug / repoSlug ──────────────────────────────────
    // Scope format is canonically "<org>/<repo>", e.g. "acme/web". The
    // /extract endpoint historically passed the workspace UUID instead,
    // which used to throw and abort the entire extractor chain. Now we
    // skip cleanly (no nodes/edges written) and let other extractors run.
    const [orgSlug, repoSlug] = ctx.scope.split("/");
    if (!orgSlug || !repoSlug) {
      ctx.log?.warn?.(
        `GitExtractor: scope "${ctx.scope}" is not "<org>/<repo>" — skipping. ` +
        `Pass scope=org/repo on the /extract request to enable Git extraction.`
      );
      return { nodesWritten: 0, edgesWritten: 0, warnings: [
        `git skipped — scope "${ctx.scope}" not in org/repo format`,
      ] };
    }

    // ── 1. Organisation node ──────────────────────────────────────────────
    const orgId = buildUrn({ source: "repo", scope: ctx.scope, artifact: `org/${orgSlug}` });
    const orgNode: NodeEnvelope = {
      id:           orgId,
      type:         "Organization",
      name:         orgSlug,
      qualified_name: orgSlug,
      source_uri:   ctx.repoRoot,
      source_checksum: md5(orgSlug),
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "api",
      created_at_commit:    ctx.commitSha,
      last_modified_commit: ctx.commitSha,
      valid_from_commit:    ctx.commitSha,
      valid_to_commit:      null,
      status:       "active",
      attributes:   { vcs_host: "git" },
    };
    await ctx.graph.upsertNode(orgNode);
    nodesWritten++;

    // ── 2. Repository node ────────────────────────────────────────────────
    const repoId = Urn.repository(ctx.scope);
    const remotes = await safeCall(() => git.getRemotes(true), warnings, []);
    const originUrl = remotes.find(r => r.name === "origin")?.refs?.fetch ?? ctx.repoRoot;

    // Detect primary language heuristically from file extensions
    const primaryLang = await detectPrimaryLanguage(git, warnings);

    // Detect default branch
    let defaultBranch = "main";
    try {
      const refs = await git.listRemote(["--symref", "origin", "HEAD"]);
      const m = refs.match(/ref: refs\/heads\/(\S+)\s+HEAD/);
      if (m?.[1]) defaultBranch = m[1];
    } catch {
      // fallback: check if main or master exists
      const branches = await safeCall(() => git.branchLocal(), warnings, { all: [] as string[] });
      if ((branches.all as string[]).includes("master")) defaultBranch = "master";
    }

    const repoNode: NodeEnvelope = {
      id:           repoId,
      type:         "Repository",
      name:         repoSlug,
      qualified_name: ctx.scope,
      source_uri:   originUrl,
      source_checksum: md5(originUrl),
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "api",
      created_at_commit:    ctx.commitSha,
      last_modified_commit: ctx.commitSha,
      valid_from_commit:    ctx.commitSha,
      valid_to_commit:      null,
      status:       "active",
      attributes: {
        default_branch:   defaultBranch,
        vcs_url:          originUrl,
        primary_language: primaryLang ?? undefined,
      },
    };
    await ctx.graph.upsertNode(repoNode);
    nodesWritten++;

    // Org → CONTAINS → Repo edge
    const orgRepoEdge: EdgeEnvelope = {
      id:          `${orgId}>>contains>>${repoId}`,
      type:        "contains",
      source_id:   orgId,
      target_id:   repoId,
      cardinality: "1-n",
      source_uri:  ctx.repoRoot,
      extractor,
      derivation:  "api",
      confidence:  1.0,
      valid_from_commit: ctx.commitSha,
      valid_to_commit:   null,
      attributes:  {},
    };
    await ctx.graph.upsertEdge(orgRepoEdge);
    edgesWritten++;

    // ── 3. Branches ───────────────────────────────────────────────────────
    const branchInfo = await safeCall(() => git.branchLocal(), warnings, { all: [] as string[], current: "" });
    const localBranches: string[] = branchInfo.all as string[];

    const branchNodes: NodeEnvelope[] = localBranches.map(branchName => ({
      id:           Urn.branch(ctx.scope, branchName),
      type:         "Branch",
      name:         branchName,
      qualified_name: `${ctx.scope}@${branchName}`,
      source_uri:   ctx.repoRoot,
      source_checksum: md5(branchName),
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "api",
      created_at_commit:    ctx.commitSha,
      last_modified_commit: ctx.commitSha,
      valid_from_commit:    ctx.commitSha,
      valid_to_commit:      null,
      status:       "active",
      attributes:   {
        is_default: branchName === defaultBranch,
      },
    }));

    const { written: bw, errors: be } = await ctx.graph.upsertNodes(branchNodes);
    nodesWritten += bw;
    warnings.push(...be);

    // Repo → CONTAINS → Branch edges
    const branchEdges: EdgeEnvelope[] = localBranches.map(branchName => {
      const branchId = Urn.branch(ctx.scope, branchName);
      return {
        id:          `${repoId}>>contains>>${branchId}`,
        type:        "contains" as const,
        source_id:   repoId,
        target_id:   branchId,
        cardinality: "1-n" as const,
        source_uri:  ctx.repoRoot,
        extractor,
        derivation:  "api" as const,
        confidence:  1.0,
        valid_from_commit: ctx.commitSha,
        valid_to_commit:   null,
        attributes:  {},
      };
    });
    const { written: beW, errors: beE } = await ctx.graph.upsertEdges(branchEdges);
    edgesWritten += beW;
    warnings.push(...beE);

    // ── 4. Commits (last N on current branch) ─────────────────────────────
    const log: LogResult = await safeCall(
      () => git.log(["--max-count", String(DEFAULT_COMMIT_DEPTH)]),
      warnings,
      { all: [], total: 0, latest: null }
    );

    const commitNodes: NodeEnvelope[] = (log.all as Array<{
      hash: string; message: string; author_name: string;
      author_email: string; date: string; body: string;
    }>).map(c => ({
      id:           Urn.commit(ctx.scope, c.hash),
      type:         "Commit",
      name:         c.hash.slice(0, 12),
      qualified_name: `${ctx.scope}@${c.hash}`,
      source_uri:   ctx.repoRoot,
      source_checksum: c.hash,
      extractor,
      extraction_timestamp: now,
      confidence:   1.0,
      derivation:   "api",
      created_at_commit:    c.hash,
      last_modified_commit: c.hash,
      valid_from_commit:    c.hash,
      valid_to_commit:      null,
      status:       "active",
      attributes: {
        sha:          c.hash,
        message:      c.message.slice(0, 512),
        author_name:  c.author_name,
        author_email: c.author_email,
        timestamp:    c.date,
      },
    }));

    const { written: cw, errors: ce } = await ctx.graph.upsertNodes(commitNodes);
    nodesWritten += cw;
    warnings.push(...ce);

    // Current branch → BELONGS_TO_BRANCH for each commit
    const currentBranch = branchInfo.current ?? defaultBranch;
    const currentBranchId = Urn.branch(ctx.scope, currentBranch);
    const commitEdges: EdgeEnvelope[] = (log.all as Array<{ hash: string }>).map(c => {
      const commitId = Urn.commit(ctx.scope, c.hash);
      return {
        id:          `${commitId}>>belongs_to_branch>>${currentBranchId}`,
        type:        "belongs_to_branch" as const,
        source_id:   commitId,
        target_id:   currentBranchId,
        cardinality: "n-1" as const,
        source_uri:  ctx.repoRoot,
        extractor,
        derivation:  "api" as const,
        confidence:  1.0,
        valid_from_commit: c.hash,
        valid_to_commit:   null,
        attributes:  {},
      };
    });

    const { written: ceW, errors: ceE } = await ctx.graph.upsertEdges(commitEdges);
    edgesWritten += ceW;
    warnings.push(...ceE);

    return {
      nodesWritten,
      edgesWritten,
      nodesInvalidated: 0,
      warnings,
      durationMs: Date.now() - t0,
    };
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function md5(input: string): string {
  return crypto.createHash("md5").update(input).digest("hex");
}

async function safeCall<T>(
  fn: () => Promise<T>,
  warnings: string[],
  fallback: T
): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    warnings.push(String(e));
    return fallback;
  }
}

/**
 * Naive primary language detection based on file extension counts.
 * Returns the extension with the most files, or null if git ls-files fails.
 */
async function detectPrimaryLanguage(
  git: SimpleGit,
  warnings: string[]
): Promise<string | null> {
  const extMap: Record<string, string> = {
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".py": "Python",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".cs": "C#",
    ".cpp": "C++", ".cc": "C++",
    ".c": "C",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".php": "PHP",
  };
  try {
    const raw = await git.raw(["ls-files"]);
    const counts: Record<string, number> = {};
    for (const line of raw.split("\n")) {
      const ext = line.slice(line.lastIndexOf("."));
      if (extMap[ext]) counts[ext] = (counts[ext] ?? 0) + 1;
    }
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    return top ? extMap[top[0]] ?? null : null;
  } catch (e) {
    warnings.push(`detectPrimaryLanguage: ${e}`);
    return null;
  }
}
