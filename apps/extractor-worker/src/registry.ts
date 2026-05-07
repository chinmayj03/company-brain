/**
 * apps/extractor-worker/src/registry.ts
 *
 * Authoritative list of registered extractors.
 * Execution order matters — see comments.
 *
 * Pass 1: source-of-truth extractors (git, ts-ast)
 * Pass 2: framework extractors (run after core-ts so symbol nodes exist)
 * Pass 2b: universal DB schema extractors (language-agnostic)
 * Pass 3: post-pass (DriftDetector runs last — needs implements_contract edges)
 */

import { GitExtractor }                    from "@company-brain/extractor-git";
import { CoreTsExtractor }                 from "@company-brain/extractor-core-ts";
import { FrameworkNextExtractor }          from "@company-brain/extractor-framework-next";
import { FrameworkPrismaExtractor }        from "@company-brain/extractor-framework-prisma";
import { FrameworkOpenApiExtractor }       from "@company-brain/extractor-framework-openapi";
import { FrameworkSqlExtractor }           from "@company-brain/extractor-framework-sql";
import { FrameworkJpaExtractor }           from "@company-brain/extractor-framework-jpa";
import { FrameworkSqlAlchemyExtractor }    from "@company-brain/extractor-framework-sqlalchemy";
import { DriftDetector }                   from "@company-brain/drift-detector";

export const EXTRACTORS = [
  // ── Pass 1: source-of-truth ──────────────────────────────────────────────
  new GitExtractor(),
  new CoreTsExtractor(),

  // ── Pass 2: JS/TS framework extractors ──────────────────────────────────
  new FrameworkNextExtractor(),
  new FrameworkPrismaExtractor(),
  new FrameworkOpenApiExtractor(),

  // ── Pass 2b: universal DB schema extractors ──────────────────────────────
  new FrameworkSqlExtractor(),
  new FrameworkJpaExtractor(),
  new FrameworkSqlAlchemyExtractor(),

  // ── Pass 3: post-extractors ──────────────────────────────────────────────
  new DriftDetector(),
];
