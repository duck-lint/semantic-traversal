"""Pure semantic-grounding authority for the typed retrieval runtime.

The compiler describes a request; this module gives that description a small,
deterministic authority model.  It deliberately does not know what any
frontmatter field means and does not perform retrieval or graph I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
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
    original_value: str | None = None
    subject_ids: tuple[str, ...] = ()
    subject_spans: tuple[str, ...] = ()
    predicate_residual: str = ""
    role: str = "query_level_context"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "original_value": self.original_value or self.value,
            "source": self.source,
            "subject_id": self.subject_id,
            "subject_ids": list(self.subject_ids),
            "subject_spans": list(self.subject_spans),
            "predicate_residual": self.predicate_residual,
            "role": self.role,
        }


@dataclass(frozen=True)
class SubjectPropositionBundle:
    subject_id: str
    referent: str | None
    referent_atoms: tuple[GroundingAtom, ...] = ()
    supporting_context_atoms: tuple[GroundingAtom, ...] = ()
    shared_context_atoms: tuple[GroundingAtom, ...] = ()
    subject_bearing_atoms: tuple[GroundingAtom, ...] = ()
    subject_predicate_atoms: tuple[GroundingAtom, ...] = ()
    shared_predicate_atoms: tuple[GroundingAtom, ...] = ()
    query_level_atoms: tuple[GroundingAtom, ...] = ()
    requested_relations: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "referent": self.referent,
            "referent_atoms": [atom.as_dict() for atom in self.referent_atoms],
            "supporting_context_atoms": [atom.as_dict() for atom in self.supporting_context_atoms],
            "shared_context_atoms": [atom.as_dict() for atom in self.shared_context_atoms],
            "subject_bearing_atoms": [atom.as_dict() for atom in self.subject_bearing_atoms],
            "subject_predicate_atoms": [atom.as_dict() for atom in self.subject_predicate_atoms],
            "shared_predicate_atoms": [atom.as_dict() for atom in self.shared_predicate_atoms],
            "query_level_atoms": [atom.as_dict() for atom in self.query_level_atoms],
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


def _raw_context_atoms(plan: dict[str, Any]) -> list[tuple[str, str, str]]:
    atoms: list[tuple[str, str, str]] = []
    for field_name, kind in (("concepts", "concept"), ("semantic_queries", "semantic_query"), ("lexical_queries", "lexical_query"), ("graph_seeds", "graph_seed")):
        atoms.extend((kind, value, field_name) for value in _strings(plan.get(field_name)))
    for entry in plan.get("literal_terms") or []:
        if isinstance(entry, dict):
            value = str(entry.get("term") or "").strip()
            if value:
                atoms.append(("literal_term", value, "literal_terms"))
    return atoms


_INERT_RESIDUE = {"and", "or", "vs", "versus", "regarding", "about", "between", "for", "of", "the"}


def _decompose_atom(*, kind: str, value: str, source: str, referents: list[str], named_subjects: bool) -> GroundingAtom:
    """Split exact referent spans from one compiler atom without inference."""
    matches: list[tuple[int, int, int, str]] = []
    # Longest spans win when one referent is contained in another.  Subject IDs
    # are restored to compiler order after matching.
    for subject_index, referent in sorted(enumerate(referents), key=lambda item: (-len(item[1]), item[0])):
        words = [part for part in _norm(referent).split() if part]
        if not words:
            continue
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"
        for match in re.finditer(pattern, value.casefold()):
            if any(match.start() < end and start < match.end() for start, end, _, _ in matches):
                continue
            matches.append((match.start(), match.end(), subject_index, value[match.start():match.end()]))
    matches.sort(key=lambda item: item[0])
    subject_ids = tuple(f"subject-{index}" for index in sorted({item[2] for item in matches}))
    spans = tuple(item[3] for item in matches)
    residual = value
    for start, end, _, _ in reversed(matches):
        residual = residual[:start] + " " + residual[end:]
    residual = " ".join(residual.split())
    residual_words = [word.strip(".,:;!?()[]{}") for word in residual.casefold().split()]
    if residual_words and all(word in _INERT_RESIDUE for word in residual_words):
        residual = ""
    if matches and residual:
        role = "subject_and_predicate"
    elif matches:
        role = "subject_only"
    elif named_subjects:
        role = "predicate_only"
    else:
        role = "query_level_context"
    return GroundingAtom(
        kind=kind,
        value=residual or value,
        source=source,
        subject_id=subject_ids[0] if len(subject_ids) == 1 else None,
        original_value=value,
        subject_ids=subject_ids,
        subject_spans=spans,
        predicate_residual=residual,
        role=role,
    )


def build_grounding_spec(plan: dict[str, Any]) -> GroundingSpecification:
    """Interpret only the compiler's existing fields into proposition bundles."""
    referents = _strings(plan.get("resolved_referents"))
    raw_context = _raw_context_atoms(plan)
    context = [_decompose_atom(kind=kind, value=value, source=source, referents=referents, named_subjects=bool(referents)) for kind, value, source in raw_context]
    relations = _unique(
        str(layer.get("operator") or "").strip()
        for layer in plan.get("retrieval_layers") or []
        if isinstance(layer, dict)
    )
    required = _strings(plan.get("evidence_requirements"))
    if not referents:
        bundle = SubjectPropositionBundle(
            subject_id="query-0", referent=None,
            supporting_context_atoms=tuple(context), query_level_atoms=tuple(context),
            requested_relations=tuple(relations), required_evidence=tuple(required),
        )
        return GroundingSpecification((bundle,), False, tuple(context), tuple(relations), tuple(required))

    # Predicate-only atoms are the only genuinely shared context.  A
    # subject-and-predicate atom carries an explicit subject scope and must be
    # assigned only to the subjects named in that atom.
    shared = tuple(atom for atom in context if atom.role == "predicate_only") if len(referents) > 1 else ()
    bundles: list[SubjectPropositionBundle] = []
    for index, referent in enumerate(referents):
        subject_id = f"subject-{index}"
        bearing = tuple(atom for atom in context if subject_id in atom.subject_ids)
        subject_predicate = tuple(atom for atom in bearing if atom.role == "subject_and_predicate" and atom.predicate_residual)
        shared_predicate = tuple(shared)
        predicate_atoms = tuple([*subject_predicate, *shared_predicate])
        # Compatibility field retains only atoms usable as context; subject-only
        # atoms are intentionally excluded from predicate admission.
        supporting = tuple(predicate_atoms) if len(referents) > 1 else tuple(
            atom for atom in context if atom.role in {"predicate_only", "subject_and_predicate"}
        )
        # With one subject, compiler context is naturally associated with it.
        bundles.append(SubjectPropositionBundle(
            subject_id=f"subject-{index}", referent=referent,
            referent_atoms=(GroundingAtom("referent", referent, "resolved_referents", subject_id, original_value=referent, subject_ids=(subject_id,), subject_spans=(referent,), role="subject_only"),),
            supporting_context_atoms=supporting,
            shared_context_atoms=shared,
            subject_bearing_atoms=bearing,
            subject_predicate_atoms=subject_predicate,
            shared_predicate_atoms=shared_predicate,
            query_level_atoms=(),
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
    value = (atom.predicate_residual or atom.value).casefold()
    if not value:
        return None
    text = _candidate_text(candidate).casefold()
    if value in text:
        return {"kind": atom.kind, "value": atom.value, "original_value": atom.original_value or atom.value, "source": atom.source, "role": atom.role, "match": "contextual_text"}
    provenance_fields = ("exact_term_provenance", "semantic_query_provenance", "context_probe_provenance", "graph_provenance")
    if any(value == str(item).casefold() or value in str(item).casefold() for field_name in provenance_fields for item in _strings(candidate.get(field_name))):
        return {"kind": atom.kind, "value": atom.value, "original_value": atom.original_value or atom.value, "source": atom.source, "role": atom.role, "match": "retrieval_provenance"}
    return None


def classify_candidate(candidate: dict[str, Any], specification: GroundingSpecification) -> dict[str, Any]:
    """Return a serializable authority assessment for one retrieved candidate."""
    assessment: dict[str, Any] = {
        "object_identity": {"subjects": [], "evidence": []},
        "evidence_unit": {"subjects": [], "context_atoms": [], "evidence": []},
        "relation_proposition": {"subjects": [], "relations": [], "context_atoms": [], "eligible": False},
        "graph_authority": {"may_seed_subject_graph": False, "may_propagate_subjects": [], "query_level_only": not specification.named_subjects},
        "subject_evidence": {},
        "predicate_evidence": {},
        "rejection_reasons": [],
    }
    identity_by_subject: dict[str, list[dict[str, str]]] = {}
    evidence_by_subject: dict[str, list[dict[str, Any]]] = {}
    predicate_by_subject: dict[str, list[dict[str, Any]]] = {}
    subject_evidence_by_subject: dict[str, list[dict[str, Any]]] = {}
    promotions = candidate.get("wikilink_identity_promotions") if isinstance(candidate.get("wikilink_identity_promotions"), list) else []
    for bundle in specification.bundles:
        if bundle.referent is None:
            # `supporting_context_atoms` is the authoritative per-subject
            # distribution.  Do not append the shared compatibility view a
            # second time.
            atoms = list(bundle.supporting_context_atoms)
        else:
            identity = _identity_match(candidate, bundle.referent)
            identity.extend(
                {"surface": "resolved_wikilink_target", "value": bundle.referent, "target_note_id": item.get("target_note_id"), "occurrence": item.get("occurrence", {})}
                for item in promotions if str(item.get("subject_id")) == bundle.subject_id
            )
            identity_by_subject[bundle.subject_id] = identity
            if identity:
                assessment["object_identity"]["subjects"].append(bundle.subject_id)
                assessment["object_identity"]["evidence"].extend({**item, "subject_id": bundle.subject_id} for item in identity)
            atoms = [*bundle.supporting_context_atoms, *bundle.shared_context_atoms]
            subject_atoms = bundle.subject_bearing_atoms
            subject_matches: list[dict[str, Any]] = []
            candidate_text = _candidate_text(candidate).casefold()
            for atom in subject_atoms:
                for span in atom.subject_spans or ((bundle.referent or ""),):
                    if span and re.search(rf"(?<!\w){re.escape(span.casefold())}(?!\w)", candidate_text):
                        subject_matches.append({"kind": atom.kind, "value": span, "source": atom.source, "role": atom.role, "match": "subject_span"})
                        break
            if subject_matches:
                subject_evidence_by_subject[bundle.subject_id] = subject_matches
            elif len(specification.bundles) == 1 and bundle.referent and re.search(
                rf"(?<!\w){re.escape(bundle.referent.casefold())}(?!\w)", candidate_text
            ):
                subject_evidence_by_subject[bundle.subject_id] = [{
                    "kind": "descriptive_subject", "value": bundle.referent,
                    "source": "candidate_text", "role": "subject_only", "match": "descriptive_subject",
                }]
            elif len(specification.bundles) == 1:
                # Consume an existing bounded atom for descriptive association
                # without inventing an alias, identity, or graph seed.
                bounded_subject_matches = [
                    match for atom in (*bundle.subject_bearing_atoms, *bundle.supporting_context_atoms)
                    if atom.role != "subject_only" and (match := _atom_matches(candidate, atom))
                ]
                if bounded_subject_matches:
                    subject_evidence_by_subject[bundle.subject_id] = bounded_subject_matches
        matched = [match for atom in atoms if atom.role != "subject_only" and (match := _atom_matches(candidate, atom))]
        predicate_matches = [match for atom in atoms if atom.role in {"predicate_only", "subject_and_predicate"} and atom.predicate_residual and (match := _atom_matches(candidate, atom))]

        # A resolved, admitted frontmatter wikilink plus its authored graph
        # edge is already typed relational evidence.  The source prose does
        # not need to repeat the field's natural-language meaning.
        if bundle.referent is not None:
            typed_match = _typed_relation_match(candidate, bundle.subject_id)
            if typed_match is not None:
                matched.append(typed_match)
                predicate_matches.append(typed_match)
        if matched:
            evidence_by_subject[bundle.subject_id] = matched
            assessment["evidence_unit"]["subjects"].append(bundle.subject_id)
            assessment["evidence_unit"]["context_atoms"].extend(matched)
            if not specification.named_subjects:
                predicate_by_subject[bundle.subject_id] = list(matched)
        if predicate_matches:
            predicate_by_subject[bundle.subject_id] = predicate_matches
    # Existing graph provenance is trusted only as an explicit relation from an
    # identity-grounded seed; arbitrary context_subjects are not authority.
    raw_graph_subjects = _strings(candidate.get("authorized_subjects"))
    referent_to_subject = {bundle.referent: bundle.subject_id for bundle in specification.bundles if bundle.referent}
    graph_subjects = [referent_to_subject.get(value, value) for value in raw_graph_subjects]
    # A descriptive subject can be proposition-grounded by its supporting
    # context, but it never gains graph-seeding authority from that fact.
    descriptive_subjects = [subject_id for subject_id in subject_evidence_by_subject if not specification.named_subjects or len(specification.bundles) == 1]
    associated_subjects = _unique([*assessment["object_identity"]["subjects"], *graph_subjects, *descriptive_subjects])
    if len(specification.bundles) > 1:
        associated_subjects = _unique([*associated_subjects, *subject_evidence_by_subject.keys()])
    if associated_subjects:
        assessment["evidence_unit"]["subjects"] = _unique([*assessment["evidence_unit"]["subjects"], *associated_subjects])
    for subject_id in associated_subjects:
        subject_evidence_by_subject.setdefault(subject_id, [{
            "kind": "subject_authority", "value": subject_id,
            "source": "object_identity" if subject_id in assessment["object_identity"]["subjects"] else "authorized_graph",
            "role": "subject_only", "match": "authority",
        }])
    if assessment["object_identity"]["subjects"] or graph_subjects:
        assessment["graph_authority"]["may_seed_subject_graph"] = bool(assessment["object_identity"]["subjects"])
        assessment["graph_authority"]["may_propagate_subjects"] = associated_subjects
    for bundle in specification.bundles:
        if not specification.named_subjects:
            # Query-level context has no named object to ground.  Its
            # proposition is admitted directly from matched query context.
            predicate_matches = [*evidence_by_subject.get(bundle.subject_id, [])]
            if predicate_matches:
                assessment["relation_proposition"]["subjects"].append(bundle.subject_id)
                assessment["relation_proposition"]["context_atoms"].extend(predicate_matches)
            continue
        if bundle.subject_id not in associated_subjects:
            assessment["rejection_reasons"].append("missing_subject_grounding")
            continue
        subject_grounded = bundle.subject_id in associated_subjects
        predicate_matches = predicate_by_subject.get(bundle.subject_id, [])
        if not predicate_matches:
            assessment["rejection_reasons"].append("missing_predicate_grounding")
            assessment["rejection_reasons"].append("missing_supporting_context")
            continue
        assessment["relation_proposition"]["subjects"].append(bundle.subject_id)
        assessment["relation_proposition"]["context_atoms"].extend(predicate_matches)
    assessment["relation_proposition"]["subjects"] = _unique(assessment["relation_proposition"]["subjects"])
    subject_to_referent = {bundle.subject_id: bundle.referent for bundle in specification.bundles}
    assessment["relation_proposition"]["subject_referents"] = [
        subject_to_referent[subject_id]
        for subject_id in assessment["relation_proposition"]["subjects"]
        if subject_to_referent.get(subject_id)
    ]
    assessment["relation_proposition"]["relations"] = list(specification.requested_relations)
    assessment["relation_proposition"]["eligible"] = bool(assessment["relation_proposition"]["subjects"])
    assessment["subject_evidence"] = {key: value for key, value in subject_evidence_by_subject.items() if value}
    assessment["predicate_evidence"] = {key: value for key, value in predicate_by_subject.items() if value}
    assessment["subject_referents_by_id"] = {
        bundle.subject_id: bundle.referent
        for bundle in specification.bundles
        if bundle.referent is not None
    }
    assessment["diagnostic_subjects"] = {
        "subject_grounded": sorted(associated_subjects),
        "predicate_grounded": sorted(predicate_by_subject),
        "proposition_grounded": sorted(assessment["relation_proposition"]["subjects"]),
    }
    if specification.named_subjects and not assessment["relation_proposition"]["eligible"]:
        assessment["rejection_reasons"].append("subject_or_supporting_context_not_grounded")
    if not specification.named_subjects and assessment["relation_proposition"]["eligible"]:
        assessment["graph_authority"]["query_level_only"] = True
    return assessment


def _typed_relation_match(candidate: dict[str, Any], subject_id: str) -> dict[str, Any] | None:
    """Consume existing authored-link provenance as typed relation evidence."""
    promotions = candidate.get("wikilink_identity_promotions")
    hops = candidate.get("graph_hop_provenance")
    if not isinstance(promotions, list) or not isinstance(hops, list):
        return None
    for promotion in promotions:
        if not isinstance(promotion, dict) or str(promotion.get("subject_id")) != subject_id:
            continue
        occurrence = promotion.get("occurrence") if isinstance(promotion.get("occurrence"), dict) else {}
        if (
            occurrence.get("source_surface") != "admitted_frontmatter"
            or not str(occurrence.get("frontmatter_field_path") or "").strip()
            or occurrence.get("resolution_status") != "resolved"
            or not str(promotion.get("target_note_id") or "").strip()
        ):
            continue
        for hop in hops:
            if not isinstance(hop, dict) or hop.get("edge_type") != "note_links_note":
                continue
            propagated = _strings(hop.get("propagated_subjects"))
            edge_provenance = hop.get("edge_provenance") if isinstance(hop.get("edge_provenance"), dict) else {}
            authored_edge = edge_provenance.get("source_surface") == "admitted_frontmatter" and bool(str(edge_provenance.get("frontmatter_field_path") or "").strip())
            if not authored_edge and isinstance(edge_provenance.get("provenance"), list):
                authored_edge = any(
                    isinstance(record, dict)
                    and record.get("source_surface") == "admitted_frontmatter"
                    and bool(str(record.get("frontmatter_field_path") or "").strip())
                    for record in edge_provenance["provenance"]
                )
            if not hop.get("subject_propagation_authorized") or subject_id not in propagated:
                continue
            if not authored_edge:
                continue
            return {
                "kind": "typed_relation_evidence",
                "value": str(occurrence.get("frontmatter_field_path")),
                "original_value": str(occurrence.get("raw_wikilink_text") or ""),
                "source": "admitted_frontmatter",
                "role": "typed_relation",
                "match": "typed_relation_provenance",
                "subject_id": subject_id,
                "target_note_id": str(promotion.get("target_note_id")),
                "occurrence": occurrence,
                "graph_hop": hop,
                "edge_provenance": edge_provenance,
            }
    return None


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
    for evidence_key in ("subject_evidence", "predicate_evidence"):
        target = merged.setdefault(evidence_key, {})
        source = right.get(evidence_key) if isinstance(right.get(evidence_key), dict) else {}
        for subject_id, evidence in source.items():
            if not isinstance(evidence, list):
                continue
            existing = target.setdefault(subject_id, [])
            for item in evidence:
                if item not in existing:
                    existing.append(item)
    merged["subject_referents_by_id"] = {
        **(left.get("subject_referents_by_id") if isinstance(left.get("subject_referents_by_id"), dict) else {}),
        **(right.get("subject_referents_by_id") if isinstance(right.get("subject_referents_by_id"), dict) else {}),
    }
    reasons = merged.setdefault("rejection_reasons", [])
    for reason in _strings(right.get("rejection_reasons")):
        if reason not in reasons:
            reasons.append(reason)
    relation = merged["relation_proposition"]
    # Eligibility is derived from the merged structured records, never from a
    # compatibility boolean supplied by one retrieval route.
    subject_evidence = merged.get("subject_evidence") if isinstance(merged.get("subject_evidence"), dict) else {}
    predicate_evidence = merged.get("predicate_evidence") if isinstance(merged.get("predicate_evidence"), dict) else {}
    eligible_subjects = [subject_id for subject_id in subject_evidence if subject_evidence.get(subject_id) and predicate_evidence.get(subject_id)]
    # Query-level propositions intentionally have no subject record; matched
    # query context is sufficient evidence for their single internal bundle.
    if "query-0" in predicate_evidence and not subject_evidence.get("query-0"):
        eligible_subjects.append("query-0")
    relation["subjects"] = _unique(eligible_subjects)
    referents_by_id = merged.get("subject_referents_by_id") if isinstance(merged.get("subject_referents_by_id"), dict) else {}
    relation["subject_referents"] = [str(referents_by_id[subject_id]) for subject_id in relation["subjects"] if referents_by_id.get(subject_id)]
    relation["eligible"] = bool(relation.get("subjects"))
    return merged


def proposition_subjects(assessment: dict[str, Any]) -> list[str]:
    relation = assessment.get("relation_proposition") if isinstance(assessment, dict) else {}
    return _unique(relation.get("subjects") if isinstance(relation, dict) else [])
