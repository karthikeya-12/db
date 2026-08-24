"""
Entity/relationship extraction from ingested content -- a simplified,
generic adaptation of the idea behind everest_repo's signal extraction
(kg_zone2/kg_signal/extractor/signal_extraction.py): pull out named things
and the assertions connecting them, in the document's own wording, with
verbatim evidence.

NOT a full port of that file's extraction+mapping pipeline -- everest_repo's
version maps every extracted participant against Everest Group's own
proprietary concept/entity registry (a business-specific vocabulary built
from their research coverage) before anything becomes a graph node; this
pipeline has no equivalent registry, so entities here are deduped by
(name, type) only, with no canonicalization against an external index.
That's a real, deliberate simplification, not an oversight.

What IS ported directly: validate_extraction()'s typed-findings approach
(rule_id/severity/detail) and its verbatim-evidence check (every evidence
span must appear exactly in the source text) -- that validation logic has
nothing to do with the SF registry and is fully generic, so it's reused
here almost as-is rather than re-derived from scratch.
"""

from __future__ import annotations

import json

PROMPT_ENTITY_RELATIONSHIP_EXTRACTION = """\
You extract entities and relationships from a document, using the document's own
wording -- never infer facts from outside knowledge, and never invent anything not
stated in the text.

An entity is a specific named thing: a person, organization, place, product/platform,
or a named concept/theme the text is substantively about. A relationship is a stated
connection between two entities, expressed as (subject, predicate, object) -- the
predicate should be a short verb phrase in the document's own words (e.g. "acquired",
"is based in", "partners with"), not a generic label.

Only extract what the text explicitly asserts. Skip boilerplate, headers, page
furniture, and generic capability claims not tied to a specific fact. Every entity
and relationship must carry a verbatim evidence span -- copied EXACTLY from the text,
no paraphrasing, no ellipses, no re-punctuation -- since evidence spans are checked
against the source text after extraction.

Return strict JSON matching this shape:
{
  "entities": [
    {"name": string, "type": "PERSON" | "ORGANIZATION" | "PLACE" | "PRODUCT" | "CONCEPT" | "OTHER", "evidence": string}
  ],
  "relationships": [
    {"subject": string, "predicate": string, "object": string, "evidence": string}
  ]
}
Empty lists if the text contains none. JSON only, no commentary.
"""

ENTITY_TYPES = {"PERSON", "ORGANIZATION", "PLACE", "PRODUCT", "CONCEPT", "OTHER"}


def _finding(
    rule_id: str, severity: str, detail: str, locus: str | None = None
) -> dict:
    return {"rule_id": rule_id, "severity": severity, "detail": detail, "locus": locus}


def validate_extraction(output: dict, source_text: str | None = None) -> list[dict]:
    """
    Validate one extraction output. Returns a list of typed findings ([] == valid) --
    same shape (rule_id/severity/detail/locus) as everest_repo's
    signal_extraction.py::validate_extraction, adapted to this project's simpler
    entities/relationships schema instead of signals/participants.

      EX-REQUIRED       top-level shape: entities + relationships lists present
      EX-ENTITY-FIELDS  every entity has name, type, evidence
      EX-ENTITY-TYPE    every entity's type is one of ENTITY_TYPES
      EX-REL-FIELDS     every relationship has subject, predicate, object, evidence
      EX-VERBATIM       (when source_text given) every evidence span appears
                        verbatim in source_text -- the extraction-side check that
                        catches an LLM paraphrasing instead of quoting
    """
    findings: list[dict] = []
    if (
        not isinstance(output, dict)
        or "entities" not in output
        or "relationships" not in output
    ):
        findings.append(
            _finding(
                "EX-REQUIRED",
                "violation",
                "output must have entities and relationships lists",
            )
        )
        return findings

    for entity in output["entities"] or []:
        locus = entity.get("name") or "<unnamed entity>"
        for key in ("name", "type", "evidence"):
            if not entity.get(key):
                findings.append(
                    _finding(
                        "EX-ENTITY-FIELDS",
                        "violation",
                        f"entity missing {key!r}",
                        locus,
                    )
                )
        if entity.get("type") and entity["type"] not in ENTITY_TYPES:
            findings.append(
                _finding(
                    "EX-ENTITY-TYPE",
                    "violation",
                    f"type {entity['type']!r} not in {ENTITY_TYPES}",
                    locus,
                )
            )
        if (
            source_text is not None
            and entity.get("evidence")
            and entity["evidence"] not in source_text
        ):
            findings.append(
                _finding(
                    "EX-VERBATIM",
                    "violation",
                    "evidence span not found verbatim in source text",
                    locus,
                )
            )

    for rel in output["relationships"] or []:
        locus = f"{rel.get('subject')} -{rel.get('predicate')}-> {rel.get('object')}"
        for key in ("subject", "predicate", "object", "evidence"):
            if not rel.get(key):
                findings.append(
                    _finding(
                        "EX-REL-FIELDS",
                        "violation",
                        f"relationship missing {key!r}",
                        locus,
                    )
                )
        if (
            source_text is not None
            and rel.get("evidence")
            and rel["evidence"] not in source_text
        ):
            findings.append(
                _finding(
                    "EX-VERBATIM",
                    "violation",
                    "evidence span not found verbatim in source text",
                    locus,
                )
            )

    return findings


class EntityExtractor:
    """Wraps an LLM chat-completion call. temperature=0 for deterministic extraction."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self.model = model

    def extract(self, text: str) -> dict:
        """Return {"entities": [...], "relationships": [...]} for one item's full text.

        Runs validate_extraction() against the LLM output before returning; any entity
        or relationship that fails validation (missing fields, bad type, or an evidence
        span that isn't verbatim in the source text) is dropped rather than passed
        through to Neo4j -- a hallucinated or paraphrased evidence span should never
        reach the graph. Findings are attached under "_validation_findings" for the
        caller to log, not raised, since a partially-valid extraction is still useful.
        """
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PROMPT_ENTITY_RELATIONSHIP_EXTRACTION},
                {"role": "user", "content": text},
            ],
        )
        raw = response.choices[0].message.content or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"entities": [], "relationships": [], "_validation_findings": []}

        entities = parsed.get("entities") or []
        relationships = parsed.get("relationships") or []

        findings = validate_extraction(
            {"entities": entities, "relationships": relationships}, source_text=text
        )

        # Entity findings are keyed by entity name; relationship findings are keyed
        # by "subject -predicate-> object" -- both rule sets (fields/type/verbatim)
        # collapse to one "drop this locus" set per kind, single pass, no re-checking.
        bad_entity_loci = {
            f["locus"]
            for f in findings
            if f["rule_id"] in ("EX-ENTITY-FIELDS", "EX-ENTITY-TYPE", "EX-VERBATIM")
        }
        bad_rel_loci = {
            f["locus"]
            for f in findings
            if f["rule_id"] in ("EX-REL-FIELDS", "EX-VERBATIM")
        }

        clean_entities = [
            e
            for e in entities
            if (e.get("name") or "<unnamed entity>") not in bad_entity_loci
        ]
        clean_relationships = [
            r
            for r in relationships
            if f"{r.get('subject')} -{r.get('predicate')}-> {r.get('object')}"
            not in bad_rel_loci
        ]

        return {
            "entities": clean_entities,
            "relationships": clean_relationships,
            "_validation_findings": findings,
        }


def get_entity_extractor() -> EntityExtractor:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    return EntityExtractor(
        api_key=settings.embedding_api_key, model=settings.entity_extraction_model
    )
