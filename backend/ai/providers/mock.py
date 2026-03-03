"""Mock AI provider — deterministic, no network calls.

Used in all tests and as the ``AI_PROVIDER=mock`` default.

Output is fully deterministic: given the same ``review_type`` and
``len(input_text) % 3`` bucket it will always produce the same set of
findings.  This keeps test assertions stable across runs.

Suggested-edit contract
-----------------------
The mock returns at least one ``suggested_edit`` per review type so that the
Suggestion-to-Revision service has actionable input during tests.

All edits target the canonical fixture body used in tests::

    # AI Test

    This document is used for AI review tests.

*  ``clarity``     → ``replace_block``  targeting ``"This document is used for AI review tests."``
*  ``security``    → ``insert_after_heading`` targeting heading ``"AI Test"``
*  ``architecture``→ ``append_block``   (always succeeds regardless of content)
*  ``full``        → all three merged
"""

from __future__ import annotations

from backend.ai.providers.base import AIReviewProvider

# Canonical substring that all fixture posts contain (used by replace_block).
MOCK_REPLACE_TARGET = "This document is used for AI review tests."
MOCK_REPLACE_PROPOSED = (
    "This document demonstrates AI review capabilities within the "
    "OpenBlog workspace layer. It is used for testing and validation."
)
MOCK_HEADING_TARGET = "AI Test"
MOCK_HEADING_INSERT = (
    "\n> **Tip:** Ensure this section is kept up-to-date as the codebase evolves.\n"
)
MOCK_APPEND_BLOCK = (
    "\n## Architecture Notes\n\n"
    "This section was appended by the AI review to summarise the described architecture."
)


class MockAIProvider(AIReviewProvider):
    """Deterministic mock that returns pre-canned findings per review type."""

    name: str = "mock"

    # ── Canned finding banks ──────────────────────────────────────────────
    _FINDINGS: dict[str, list[dict]] = {
        "clarity": [
            {
                "severity": "warn",
                "category": "clarity",
                "message": "Several sentences exceed 30 words; consider splitting them.",
                "suggested_fix": "Break long sentences at natural conjunction points.",
            },
            {
                "severity": "info",
                "category": "clarity",
                "message": "Section headings are present and descriptive.",
                "suggested_fix": None,
            },
        ],
        "security": [
            {
                "severity": "high",
                "category": "security",
                "message": "Code snippet contains a hard-coded credential pattern.",
                "suggested_fix": "Replace with an environment variable reference.",
            },
            {
                "severity": "warn",
                "category": "security",
                "message": "No input validation mentioned for the user-supplied data.",
                "suggested_fix": "Add explicit validation and sanitisation steps.",
            },
        ],
        "architecture": [
            {
                "severity": "warn",
                "category": "architecture",
                "message": "Service layer mixes persistence and business logic.",
                "suggested_fix": "Extract DB queries into a dedicated repository class.",
            },
            {
                "severity": "info",
                "category": "architecture",
                "message": "Module separation looks clean for the described scope.",
                "suggested_fix": None,
            },
        ],
    }

    # "full" returns all categories merged.
    _FINDINGS["full"] = (
        _FINDINGS["clarity"] + _FINDINGS["security"] + _FINDINGS["architecture"]
    )

    # ── Canned suggested-edit banks ───────────────────────────────────────
    # Keyed by review_type; each entry is the full ``suggested_edits_json``
    # dict.  Edits target the canonical fixture body:
    #   "# AI Test\n\nThis document is used for AI review tests."
    _SUGGESTED_EDITS: dict[str, dict] = {
        "clarity": {
            "edits": [
                {
                    "id": "clarity-1",
                    "title": "Expand the introduction paragraph",
                    "kind": "replace_block",
                    "target_hint": {"match": MOCK_REPLACE_TARGET},
                    "proposed_markdown": MOCK_REPLACE_PROPOSED,
                    "rationale": (
                        "The original introduction is terse. Adding context "
                        "improves reader orientation."
                    ),
                }
            ]
        },
        "security": {
            "edits": [
                {
                    "id": "security-1",
                    "title": "Add security note after main heading",
                    "kind": "insert_after_heading",
                    "target_hint": {"heading": MOCK_HEADING_TARGET},
                    "proposed_markdown": MOCK_HEADING_INSERT,
                    "rationale": (
                        "Explicit security guidance reduces the risk of "
                        "overlooked vulnerabilities."
                    ),
                }
            ]
        },
        "architecture": {
            "edits": [
                {
                    "id": "arch-1",
                    "title": "Append architecture summary section",
                    "kind": "append_block",
                    "target_hint": {},
                    "proposed_markdown": MOCK_APPEND_BLOCK,
                    "rationale": (
                        "A dedicated architecture notes section improves navigability."
                    ),
                }
            ]
        },
    }
    _SUGGESTED_EDITS["full"] = {
        "edits": (
            _SUGGESTED_EDITS["clarity"]["edits"]
            + _SUGGESTED_EDITS["security"]["edits"]
            + _SUGGESTED_EDITS["architecture"]["edits"]
        )
    }

    def run_review(
        self,
        input_text: str,
        review_type: str,
        context: dict,
    ) -> dict:
        """Return a deterministic mock result based on *review_type*.

        The ``metrics_json`` records the input length so tests can verify
        that the correct text was forwarded to the provider.
        """
        findings = list(self._FINDINGS.get(review_type, self._FINDINGS["full"]))

        # Let input length influence which subset is returned so tests can
        # distinguish "post body review" from "revision diff review".
        if input_text:
            bucket = len(input_text) % 3
            if bucket == 0 and len(findings) > 1:
                findings = findings[:1]
            elif bucket == 1 and len(findings) > 2:
                findings = findings[:2]
            # bucket == 2 → return all findings

        summary_md = (
            f"**Mock {review_type} review** — "
            f"{len(findings)} finding(s) detected in {len(input_text)} characters of input."
        )

        suggested_edits_json = self._SUGGESTED_EDITS.get(
            review_type, self._SUGGESTED_EDITS["full"]
        )

        return {
            "summary_md": summary_md,
            "findings_json": findings,
            "metrics_json": {
                "provider": self.name,
                "model": "mock-model-v1",
                "latency_ms": 0,
                "prompt_tokens": None,
                "output_tokens": None,
                "input_chars": len(input_text),
            },
            "suggested_edits_json": suggested_edits_json,

        }

    # ── Analytics explanation canned responses ────────────────────────────────

    _EXPLANATION_TEMPLATES: dict[str, str] = {
        "trend": (
            "**Trend analysis** — The prompt shows a measurable trajectory based on "
            "the supplied version metrics.\n\n"
            "**Observations:**\n"
            "- The benchmark average delta across the last two measured versions "
            "indicates a directional shift in model performance.\n"
            "- Rating counts suggest user engagement is consistent across versions, "
            "with no sharp drop-off after any single revision.\n"
            "- A/B win count for the most recent version is within normal variance "
            "and does not signal a regression by itself.\n\n"
            "**Suggested next steps:**\n"
            "- Run at least two additional benchmark suite passes to confirm "
            "whether the current delta trend is sustained.\n"
            "- Review the change summary for the highest-delta version to identify "
            "which prompt wording adjustments drove the improvement.\n"
            "- Consider scheduling an A/B experiment to validate any planned "
            "refinement against the current best-performing version.\n"
        ),
        "fork_rationale": (
            "**Fork ranking rationale** — Composite scores are derived from "
            "benchmark averages, vote counts, and A/B win rates.\n\n"
            "**Observations:**\n"
            "- The top-ranked fork scores highest on the normalised benchmark "
            "component, which carries 60 % of the composite weight.\n"
            "- Vote normalisation distributes the 30 % vote weight proportionally, "
            "so a fork with many votes but low benchmark scores can still rank second.\n"
            "- A/B win-rate contributes only 10 % of the composite, so forks with "
            "limited experiment data are not unfairly penalised.\n\n"
            "**Suggested next steps:**\n"
            "- Investigate the prompt wording differences between the origin and "
            "the best fork to extract actionable improvements.\n"
            "- Run a dedicated benchmark suite on any fork that scores near the top "
            "but has low execution volume to validate its ranking.\n"
            "- Consider merging the strongest fork back as a new revision of the "
            "origin prompt if its benchmark advantage is confirmed.\n"
        ),
        "version_diff": (
            "**Version diff summary** — This explanation is based on the unified "
            "diff between two consecutive prompt versions.\n\n"
            "**Observations:**\n"
            "- The diff shows textual changes that may affect how models interpret "
            "the instruction structure.\n"
            "- Additions in the diff introduce new constraints or context that "
            "could improve output specificity.\n"
            "- Removed lines may reduce noise or ambiguity, but should be validated "
            "against benchmark results before considering them permanent.\n\n"
            "**Suggested next steps:**\n"
            "- Run a benchmark suite on both the pre-change and post-change versions "
            "to quantify the impact of this diff.\n"
            "- Review whether removed context was load-bearing for any edge-case "
            "model outputs by examining past execution logs.\n"
            "- If the change improves scores, document the rationale in the "
            "release note for the new version to preserve institutional knowledge.\n"
        ),
    }

    def run_explanation(self, input_dict: dict, kind: str) -> str:
        """Return a deterministic canned explanation for *kind*."""
        template = self._EXPLANATION_TEMPLATES.get(
            kind, self._EXPLANATION_TEMPLATES["trend"]
        )
        # Append a small deterministic suffix so tests can detect the mock ran.
        input_size = len(str(input_dict))
        return f"{template}\n*[mock — input size: {input_size} chars]*"
