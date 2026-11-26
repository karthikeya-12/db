"""Scoring for the chat evaluation set (evals/cases.json). Pure functions, so they are unit-tested without any model."""
from __future__ import annotations

from dataclasses import dataclass, field

REFUSAL_MARKERS = ("couldn't find", "could not find", "can't find", "cannot find", "not in the archive", "no information", "don't have", "do not contain")


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    problems: list[str] = field(default_factory=list)


def score_case(case: dict, answer: str, sources: list[dict]) -> CaseResult:
    problems: list[str] = []
    lowered = answer.lower()
    cited_titles = {s["title"] for s in sources if s.get("cited")}

    if case.get("answerable", True):
        for needle in case.get("must_include", []):
            if needle.lower() not in lowered:
                problems.append(f"answer is missing {needle!r}")
        expected = case.get("cite")
        if expected and expected not in cited_titles:
            problems.append(f"did not cite {expected} (cited: {sorted(cited_titles) or 'nothing'})")
        if not cited_titles:
            problems.append("answer has no citations")
    else:
        if not any(marker in lowered for marker in REFUSAL_MARKERS):
            problems.append("should have said the archive does not contain the answer")
    return CaseResult(case["id"], not problems, problems)


def summarize(results: list[CaseResult]) -> dict:
    passed = sum(r.passed for r in results)
    return {"total": len(results), "passed": passed, "failed": [r.case_id for r in results if not r.passed]}
