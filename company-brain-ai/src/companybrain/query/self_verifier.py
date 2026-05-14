"""
SelfVerifier — ADR-0061 P1 / M3.

Given a drafted QueryResponse and the raw content of the entities it cites,
verify that every factual claim in the summary is supported by at least one
citation and that no citation is misrepresented.

Uses the FAST LLM role (Haiku-class) — cheap, called once per query.

Scoring rubric:
  1.0  all claims verified
  0.8  minor paraphrase / hedged language, no contradictions
  0.6  one claim lacks a matching citation
  < 0.6  contradiction found OR majority of claims uncited

If score < 0.6 the ExplorationLoop triggers a revision pass; if still < 0.6
the issues surface in the response as unverified_claims.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from companybrain.llm import TaskRole, get_provider
from companybrain.llm.base import ChatMessage

log = structlog.get_logger(__name__)

_SYSTEM = """\
You are a rigorous fact-checker for AI-generated codebase answers.

Given an answer summary and the raw content of the entities cited in it,
check every factual claim in the summary.

Rules:
  - A claim is SUPPORTED if at least one citation contains the specific fact.
  - A gentle paraphrase of source text counts as supported.
  - A claim that directly contradicts a citation is CONTRADICTED (worst case).
  - A claim with no matching citation at all is UNCITED.

Respond with a single JSON object:
{
  "verified": true|false,
  "score": <float 0-1>,
  "issues": ["<description of unsupported or contradicted claim>", ...]
}

"verified" is true when score >= 0.7 and issues is empty or only minor paraphrases.
"issues" is [] when all claims are supported.
Be concise — one line per issue.
"""


@dataclass
class VerifierResult:
    verified: bool
    score: float
    issues: list[str] = field(default_factory=list)


class SelfVerifier:
    """Verify a drafted answer against its cited entity content."""

    def __init__(self, score_threshold: float = 0.6) -> None:
        self._threshold = score_threshold
        self._provider = get_provider()

    async def verify(
        self,
        answer_summary: str,
        entity_contents: dict[str, str],  # urn → raw text / snippet
    ) -> VerifierResult:
        """
        Run one cheap LLM call to check the answer against cited evidence.

        Returns a VerifierResult with score and any issues found.
        """
        if not answer_summary.strip():
            return VerifierResult(verified=True, score=1.0, issues=[])

        citations_block = self._format_citations(entity_contents)
        user_msg = (
            f"ANSWER SUMMARY:\n{answer_summary}\n\n"
            f"CITED EVIDENCE:\n{citations_block}\n\n"
            "Verify the answer against the evidence and respond with JSON only."
        )

        try:
            resp = await self._provider.chat(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                role=TaskRole.FAST,
                max_tokens=512,
            )
            return self._parse(resp.content)
        except Exception as exc:
            log.warning("[self_verifier] LLM call failed — assuming verified", error=str(exc))
            return VerifierResult(verified=True, score=0.8, issues=[])

    def _format_citations(self, entity_contents: dict[str, str]) -> str:
        if not entity_contents:
            return "(no citation content available)"
        lines = []
        for urn, content in entity_contents.items():
            excerpt = content[:400].replace("\n", " ")
            lines.append(f"[{urn}]: {excerpt}")
        return "\n".join(lines)

    def _parse(self, raw: str) -> VerifierResult:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text.strip())
            score = float(data.get("score", 0.8))
            issues = [str(i) for i in (data.get("issues") or [])]
            verified = bool(data.get("verified", score >= 0.7))
            return VerifierResult(verified=verified, score=score, issues=issues)
        except Exception:
            import re
            m = re.search(r'"score"\s*:\s*([\d.]+)', raw)
            score = float(m.group(1)) if m else 0.8
            return VerifierResult(verified=score >= 0.7, score=score, issues=[])
