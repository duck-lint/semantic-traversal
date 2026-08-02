"""Pure semantic-grounding authority for the typed retrieval runtime.

The compiler describes a request; this module gives that description a small,
deterministic authority model.  It deliberately does not know what any
frontmatter field means and does not perform retrieval or graph I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


GROUNDING_VERSION = 1


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        text = str(value).strip()
        return [text] if text else []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("term") or item.get("query") or item.get("label") or item.get("value")
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("\\", "/").split())


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in result:
            result.append(value)
    return result


@dataclass(frozen=True)
class GroundingAtom:
    kind: str
    value: str
    source: str
    subject_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "source": self.source, "subject_id": self.subject_id}


@dataclass(frozen=True)
class SubjectPropositionBundle:
    subject_id: str
    referent: str | None
    referent_atoms: tuple[GroundingAtom, ...] = ()
    supporting_context_atoms: tuple[GroundingAtom, ...] = ()
    shared_context_atoms: tuple[GroundingAtom, ...] = ()
    requested_relations: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "referent": self.referent,
            "referent_atoms": [atom.as_dict() for atom in self.referent_atoms],
            "supporting_context_atoms": [atom.as_dict() for atom in self.supporting_context_atoms],
            "shared_context_atoms": [atom.as_dict() for atom in self.shared_context_atoms],
            "requested_relations": list(self.requested_relations),
            "required_evidence": list(self.required_evidence),
        }


@dataclass(frozen=True)
class GroundingSpecification:
    bundles: tuple[SubjectPropositionBundle, ...]
    named_subjects: bool
    shared_context_atoms: tuple[GroundingAtom, ...]
    requested_relations: tuple[str, ...]
    required_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": GROUNDING_VERSION,
            "named_subjects": self.named_subjects,
            "bundle_count": len(self.bundles),
            "bundles": [bundle.as_dict() for bundle in self.bundles],
            "shared_context_atoms": [atom.as_dict() for atom in self.shared_context_atoms],
            "requested_relations": list(self.requested_relations),
            "required_evidence": list(self.required_evidence),
        }


def _context_atoms(plan: dict[str, Any]) -> list[GroundingAtom]:
    atoms: list[GroundingAtom] = []
    for field_name, kind in (("concepts", "concept"), ("semantic_queries", "semantic_query"), ("lexical_queries", "lexical_query"), ("graph_seeds", "graph_seed")):
        atoms.extend(GroundingAtom(kind, value, field_name) for value in _strings(plan.get(field_name)))
    for entry in plan.get("literal_terms") or []:
        if isinstance(entry, dict):
            value = str(entry.get("term") or "").strip()
            if value:
                atoms.append(GroundingAtom("literal_term", value, "literal_terms"))
    return atoms


def build_grounding_spec(plan: dict[str, Any]) -> GroundingSpecification:
    """Interpret only the compiler's existing fields into proposition bundles."""
    referents = _strings(plan.get("resolved_referents"))
    context = _context_atoms(plan)
    relations = _unique(
        str(layer.get("operator") or "").strip()
        for layer in plan.get("retrieval_layers") or []
        if isinstance(layer, dict)
    )
    required = _strings(plan.get("evidence_requirements"))
    if not referents:
        bundle = SubjectPropositionBundle(
            subject_id="query-0", referent=None,
            supporting_context_atoms=tuple(context), requested_relations=tuple(relations), required_evidence=tuple(required),
        )
        return GroundingSpecification((bundle,), False, tuple(context), tuple(relations), tuple(required))

    shared = tuple(context) if len(referents) > 1 else ()
    bundles: list[SubjectPropositionBundle] = []
    for index, referent in enumerate(referents):
        # With one subject, compiler context is naturally associated with it.
        # With several, the same non-referent context is evaluated separately.
        bundles.append(SubjectPropositionBundle(
            subject_id=f"subject-{index}", referent=referent,
            referent_atoms=(GroundingAtom("referent", referent, "resolved_referents", f"subject-{index}"),),
            supporting_context_atoms=tuple(context) if len(referents) == 1 else (),
            shared_context_atoms=shared,
            requested_relations=tuple(relations), required_evidence=tuple(required),
        ))
    return GroundingSpecification(tuple(bundles), True, shared, tuple(relations), tuple(required))


def _candidate_text(candidate: dict[str, Any]) -> str:
    values = [candidate.get(field) for field in ("note_title", "relative_path", "section_path", "section_label", "paragraph_text")]
    values.append(candidate.get("frontmatter_semantics_json"))
    return " ".join(str(value or "") for value in values)


def _identity_match(candidate: dict[str, Any], referent: str) -> list[dict[str, str]]:
    target = _norm(referent)
    if not target:
        return []
    note_id = _norm(candidate.get("note_id"))
    title = _norm(candidate.get("note_title"))
    path = _norm(candidate.get("relative_path"))
    filename = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    matches: list[dict[str, str]] = []
    for surface, value in (("canonical_note_id", note_id), ("note_title", title), ("relative_path", path), ("filename", filename)):
        if value and target in {value, value.rsplit("/", 1)[-1]}:
            matches.append({"surface": surface, "value": referent})
    return matches


def _atom_matches(candidate: dict[str, Any], atom: GroundingAtom) -> dict[str, Any] | None:
    value = atom.value.casefold()
    if not value:
        return None
    text = _candidate_text(candidate).casefold()
    if value in text:
        return {"kind": atom.kind, "value": atom.value, "source": atom.source, "match": "contextual_text"}
    provenance_fields = ("exact_term_provenance", "semantic_query_provenance", "context_probe_provenance", "graph_provenance")
    if any(value == str(item).casefold() or value in str(item).casefold() for field_name in provenance_fields for item in _strings(candidate.get(field_name))):
        return {"kind": atom.kind, "value": atom.value, "source": atom.source, "match": "retrieval_provenance"}
    return None


def classify_candidate(candidate: dict[str, Any], specification: GroundingSpecification) -> dict[str, Any]:
    """Return a serializable authority assessment for one retrieved candidate."""
    assessment: dict[str, Any] = {
        "object_identity": {"subjects": [], "evidence": []},
        "evidence_unit": {"subjects": [], "context_atoms": [], "evidence": []},
        "relation_proposition": {"subjects": [], "relations": [], "context_atoms": [], "eligible": False},
        "graph_authority": {"may_seed_subject_graph": False, "may_propagate_subjects": [], "query_level_only": not specification.named_subjects},
        "rejection_reasons": [],
    }
    identity_by_subject: dict[str, list[dict[str, str]]] = {}
    evidence_by_subject: dict[str, list[dict[str, Any]]] = {}
    for bundle in specification.bundles:
        if bundle.referent is None:
            atoms = [*bundle.supporting_context_atoms, *bundle.shared_context_atoms]
        else:
            identity = _identity_match(candidate, bundle.referent)
            identity_by_subject[bundle.subject_id] = identity
            if identity:
                assessment["object_identity"]["subjects"].append(bundle.subject_id)
                assessment["object_identity"]["evidence"].extend({**item, "subject_id": bundle.subject_id} for item in identity)
            atoms = [*bundle.supporting_context_atoms, *bundle.shared_context_atoms]
        matched = [match for atom in atoms if (match := _atom_matches(candidate, atom))]
        if matched:
            evidence_by_subject[bundle.subject_id] = matched
            assessment["evidence_unit"]["subjects"].append(bundle.subject_id)
            assessment["evidence_unit"]["context_atoms"].extend(matched)
    # Existing graph provenance is trusted only as an explicit relation from an
    # identity-grounded seed; arbitrary context_subjects are not authority.
    raw_graph_subjects = _strings(candidate.get("authorized_subjects"))
    referent_to_subject = {bundle.referent: bundle.subject_id for bundle in specification.bundles if bundle.referent}
    graph_subjects = [referent_to_subject.get(value, value) for value in raw_graph_subjects]
    # A descriptive subject can be proposition-grounded by its supporting
    # context, but it never gains graph-seeding authority from that fact.
    descriptive_subjects = [subject_id for subject_id, matches in evidence_by_subject.items() if matches]
    associated_subjects = _unique([*assessment["object_identity"]["subjects"], *graph_subjects, *descriptive_subjects])
    if associated_subjects:
        assessment["evidence_unit"]["subjects"] = _unique([*assessment["evidence_unit"]["subjects"], *associated_subjects])
    if assessment["object_identity"]["subjects"] or graph_subjects:
        assessment["graph_authority"]["may_seed_subject_graph"] = bool(assessment["object_identity"]["subjects"])
        assessment["graph_authority"]["may_propagate_subjects"] = associated_subjects
    for bundle in specification.bundles:
        subjects = set(associated_subjects if specification.named_subjects else [bundle.subject_id])
        if bundle.subject_id not in subjects:
            continue
        matches = evidence_by_subject.get(bundle.subject_id, [])
        if not matches:
            assessment["rejection_reasons"].append("missing_supporting_context")
            continue
        assessment["relation_proposition"]["subjects"].append(bundle.subject_id)
        assessment["relation_proposition"]["context_atoms"].extend(matches)
    assessment["relation_proposition"]["subjects"] = _unique(assessment["relation_proposition"]["subjects"])
    subject_to_referent = {bundle.subject_id: bundle.referent for bundle in specification.bundles}
    assessment["relation_proposition"]["subject_referents"] = [
        subject_to_referent[subject_id]
        for subject_id in assessment["relation_proposition"]["subjects"]
        if subject_to_referent.get(subject_id)
    ]
    assessment["relation_proposition"]["relations"] = list(specification.requested_relations)
    assessment["relation_proposition"]["eligible"] = bool(assessment["relation_proposition"]["subjects"])
    if specification.named_subjects and not assessment["relation_proposition"]["eligible"]:
        assessment["rejection_reasons"].append("subject_or_supporting_context_not_grounded")
    if not specification.named_subjects and assessment["relation_proposition"]["eligible"]:
        assessment["graph_authority"]["query_level_only"] = True
    return assessment


def merge_grounding_assessments(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge routes without allowing a later weak route to erase authority."""
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in left.items()}
    for role in ("object_identity", "evidence_unit", "relation_proposition", "graph_authority"):
        target = merged.setdefault(role, {})
        source = right.get(role) if isinstance(right.get(role), dict) else {}
        for key, value in source.items():
            if isinstance(value, list):
                existing = target.setdefault(key, [])
                for item in value:
                    if item not in existing:
                        existing.append(item)
            elif isinstance(value, bool):
                target[key] = bool(target.get(key)) or value
            elif value is not None:
                target[key] = value
    reasons = merged.setdefault("rejection_reasons", [])
    for reason in _strings(right.get("rejection_reasons")):
        if reason not in reasons:
            reasons.append(reason)
    merged["relation_proposition"]["eligible"] = bool(merged["relation_proposition"].get("eligible"))
    return merged


def proposition_subjects(assessment: dict[str, Any]) -> list[str]:
    relation = assessment.get("relation_proposition") if isinstance(assessment, dict) else {}
    return _unique(relation.get("subjects") if isinstance(relation, dict) else [])
