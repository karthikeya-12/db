"""
Entity/relationship extraction from ingested content -- a simplified,
generic adaptation of everest_repo's signal extraction + temporal-node
pattern (kg_zone2/kg_signal/extractor/signal_extraction.py and
kg_zone3/worker/neo4j/temporal_loader.py): pull out named things and the
assertions connecting them, in the document's own wording, with verbatim
evidence, grounded to the specific chunk they came from.

NOT a full port of everest's extraction+mapping pipeline -- everest maps
every extracted participant against Everest Group's own proprietary
concept/entity registry ("Structural Foundation" / "lattice", loaded via
kg_sf_load/lattice_graph_loader.py) before anything becomes a graph node;
this pipeline has no equivalent curated registry, so entities here are
deduped by (name, type) only, with no canonicalization against an external
index. That's a real, deliberate simplification, not an oversight.

What IS ported: validate_extraction()'s typed-findings approach
(rule_id/severity/detail) and its verbatim-evidence check -- generic logic,
reused almost as-is. What's newly adapted from everest's temporal_loader.py
(not present in the earlier flat-Entity version of this file): extraction
now runs PER CHUNK, not per whole item, so every evidence span can be
checked against, and later linked to, the exact chunk it came from --
mirroring everest's TemporalNode -[:HAS_EVIDENCE]-> Chunk grounding. Without
this, a graph_store consumer has no way to know which chunk an evidence
span belongs to.
"""
from __future__ import annotations

import json

PROMPT_ENTITY_RELATIONSHIP_EXTRACTION = """\
You extract entities and relationships from ONE chunk of a document, using the
chunk's own wording -- never infer facts from outside knowledge, and never invent
anything not stated in this chunk's text. You will not see the rest of the
document, so only extract what this chunk itself asserts.

An entity is a specific named thing: a person, organization, place, product/platform,
or a named concept/theme the text is substantively about. A relationship is a stated
connection between two entities, expressed as (subject, predicate, object) -- the
predicate should be a short verb phrase in the chunk's own words (e.g. "acquired",
"is based in", "partners with"), not a generic label.

Only extract what the text explicitly asserts. Skip boilerplate, headers, page
furniture, and generic capability claims not tied to a specific fact. Every entity
and relationship must carry a verbatim evidence span -- copied EXACTLY from this
chunk's text, no paraphrasing, no ellipses, no re-punctuation -- since evidence
spans are checked against this chunk's source text after extraction.

Return strict JSON matching this shape:
{
  "entities": [
    {"name": string, "type": "PERSON" | "ORGANIZATION" | "PLACE" | "PRODUCT" | "CONCEPT" | "OTHER", "evidence": string}
  ],
  "relationships": [
    {"subject": string, "predicate": string, "object": string, "evidence": string}
  ]
}
Empty lists if this chunk contains none. JSON only, no commentary.
"""

ENTITY_TYPES = {"PERSON", "ORGANIZATION", "PLACE", "PRODUCT", "CONCEPT", "OTHER"}


def _finding(rule_id: str, severity: str, detail: str, locus: str | None = None) -> dict:
    return {"rule_id": rule_id, "severity": severity, "detail": detail, "locus": locus}


def validate_extraction(output: dict, source_text: str | None = None) -> list[dict]:
    """
    Validate one chunk's extraction output. Returns a list of typed findings ([] ==
    valid) -- same shape (rule_id/severity/detail/locus) as everest_repo's
    signal_extraction.py::validate_extraction / temporal_loader.py's
    validate_temporal_node(), adapted to this project's simpler entities/
    relationships schema instead of signals/participants or up-edge/down-edge counts.

      EX-REQUIRED       top-level shape: entities + relationships lists present
      EX-ENTITY-FIELDS  every entity has name, type, evidence
      EX-ENTITY-TYPE    every entity's type is one of ENTITY_TYPES
      EX-REL-FIELDS     every relationship has subject, predicate, object, evidence
      EX-VERBATIM       (when source_text given) every evidence span appears
                        verbatim in source_text (this chunk's text) -- the
                        extraction-side check that catches an LLM paraphrasing
                        instead of quoting
    """
    findings: list[dict] = []
    if not isinstance(output, dict) or "entities" not in output or "relationships" not in output:
        findings.append(_finding("EX-REQUIRED", "violation", "output must have entities and relationships lists"))
        return findings

    for entity in output["entities"] or []:
        locus = entity.get("name") or "<unnamed entity>"
        for key in ("name", "type", "evidence"):
            if not entity.get(key):
                findings.append(_finding("EX-ENTITY-FIELDS", "violation", f"entity missing {key!r}", locus))
        if entity.get("type") and entity["type"] not in ENTITY_TYPES:
            findings.append(
                _finding("EX-ENTITY-TYPE", "violation", f"type {entity['type']!r} not in {ENTITY_TYPES}", locus)
            )
        if source_text is not None and entity.get("evidence") and entity["evidence"] not in source_text:
            findings.append(_finding("EX-VERBATIM", "violation", "evidence span not found verbatim in source text", locus))

    for rel in output["relationships"] or []:
        locus = f"{rel.get('subject')} -{rel.get('predicate')}-> {rel.get('object')}"
        for key in ("subject", "predicate", "object", "evidence"):
            if not rel.get(key):
                findings.append(_finding("EX-REL-FIELDS", "violation", f"relationship missing {key!r}", locus))
        if source_text is not None and rel.get("evidence") and rel["evidence"] not in source_text:
            findings.append(_finding("EX-VERBATIM", "violation", "evidence span not found verbatim in source text", locus))

    return findings


class EntityExtractor:
    """Wraps an LLM chat-completion call. temperature=0 for deterministic extraction."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self.model = model

    def extract(self, chunk_text: str) -> dict:
        """Return {"entities": [...], "relationships": [...]} for ONE chunk's text.

        Runs validate_extraction() against the LLM output before returning; any entity
        or relationship that fails validation (missing fields, bad type, or an evidence
        span that isn't verbatim in this chunk's source text) is dropped rather than
        passed through to Neo4j -- a hallucinated or paraphrased evidence span should
        never reach the graph. Findings are attached under "_validation_findings" for
        the caller to log, not raised, since a partially-valid extraction is still
        useful.

        Called once per chunk (see consumers/entity_relationship_consumer.py) rather
        than once per item, so every evidence span this returns is guaranteed to be
        verbatim in a SPECIFIC chunk's text -- the caller can then safely link the
        resulting graph nodes to that exact chunk.
        """
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PROMPT_ENTITY_RELATIONSHIP_EXTRACTION},
                {"role": "user", "content": chunk_text},
            ],
        )
        raw = response.choices[0].message.content or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"entities": [], "relationships": [], "_validation_findings": []}

        entities = parsed.get("entities") or []
        relationships = parsed.get("relationships") or []

        findings = validate_extraction({"entities": entities, "relationships": relationships}, source_text=chunk_text)

        # Entity findings are keyed by entity name; relationship findings are keyed
        # by "subject -predicate-> object" -- both rule sets (fields/type/verbatim)
        # collapse to one "drop this locus" set per kind, single pass, no re-checking.
        bad_entity_loci = {
            f["locus"] for f in findings if f["rule_id"] in ("EX-ENTITY-FIELDS", "EX-ENTITY-TYPE", "EX-VERBATIM")
        }
        bad_rel_loci = {f["locus"] for f in findings if f["rule_id"] in ("EX-REL-FIELDS", "EX-VERBATIM")}

        clean_entities = [e for e in entities if (e.get("name") or "<unnamed entity>") not in bad_entity_loci]
        clean_relationships = [
            r
            for r in relationships
            if f"{r.get('subject')} -{r.get('predicate')}-> {r.get('object')}" not in bad_rel_loci
        ]

        return {"entities": clean_entities, "relationships": clean_relationships, "_validation_findings": findings}


def get_entity_extractor() -> EntityExtractor:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    return EntityExtractor(api_key=settings.embedding_api_key, model=settings.entity_extraction_model)
