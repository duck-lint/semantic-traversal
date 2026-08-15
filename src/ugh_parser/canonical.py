"""Completed in-memory canonical ingest representation."""

from __future__ import annotations

from dataclasses import dataclass

from .materialize import MaterializedCorpus, MaterializedObject, MaterializedUnit, RelationCandidate
from .parser import Embed, FrontmatterField, HeadingRegion
from .resolve import RegionTarget, ResolutionResult, ResolvedObjectRelation, ResolvedRelation
from .vault import VaultParseResult


class CanonicalizationError(ValueError):
    """The supplied resolution result cannot become a completed ingest."""


@dataclass(frozen=True)
class CanonicalRegionReference:
    """A complete object-local identity for one canonical region."""

    source_object_uuid: str
    region_path: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalRegion:
    """Canonical owner of authored and parsed heading facts."""

    reference: CanonicalRegionReference
    level: int
    raw_markdown: str
    parsed_text: str
    address_text: str
    parent_region_id: str | None


@dataclass(frozen=True)
class CanonicalObject:
    """Canonical object topology retained in parsed corpus order."""

    source_object_uuid: str
    source_path: str
    path_hierarchy: tuple[str, ...]
    admitted_identifiers: tuple[FrontmatterField, ...]
    relations: tuple[CanonicalObjectRelation, ...]
    regions: tuple[CanonicalRegion, ...]


@dataclass(frozen=True)
class CanonicalObjectRelation:
    """One authored frontmatter relation occurrence owned by an object."""

    relation_name: str
    origin: str
    source_field: str | None
    raw: str
    authored_target: str
    authored_label: str
    authored_region_fragment: str | None
    source_object_uuid: str
    source_path: str
    target_object_uuid: str
    target_region: CanonicalRegionReference | None


@dataclass(frozen=True)
class CanonicalRelation:
    """One resolved authored relation occurrence, never deduplicated."""

    relation_name: str
    origin: str
    source_field: str | None
    raw: str
    authored_target: str
    authored_label: str
    authored_region_fragment: str | None
    source_object_uuid: str
    source_path: str
    source_local_order: int
    target_object_uuid: str
    target_region: CanonicalRegionReference | None


@dataclass(frozen=True)
class CanonicalUnit:
    """The canonical semantic unit owned by the completed ingest."""

    unit_id: int
    source_object_uuid: str
    source_path: str
    source_local_order: int
    path_hierarchy: tuple[str, ...]
    region_path: tuple[CanonicalRegionReference, ...]
    raw_markdown: str
    parsed_text: str
    inherited_identifiers: tuple[FrontmatterField, ...]
    relations: tuple[CanonicalRelation, ...]
    embeds: tuple[Embed, ...]


@dataclass(frozen=True)
class CompletedIngest:
    """A valid, deterministic, in-memory canonical ingest."""

    objects: tuple[CanonicalObject, ...]
    regions: tuple[CanonicalRegion, ...]
    units: tuple[CanonicalUnit, ...]
    parsed_corpus: VaultParseResult
    materialized_corpus: MaterializedCorpus
    resolution_result: ResolutionResult


def _canonical_region(
    source_object_uuid: str,
    region: HeadingRegion,
) -> CanonicalRegion:
    reference = CanonicalRegionReference(
        source_object_uuid,
        region.region_path + (region.region_id,),
    )
    return CanonicalRegion(
        reference,
        region.level,
        region.raw_markdown,
        region.parsed_text,
        region.address_text,
        region.parent_region_id,
    )


def _region_reference(
    references: dict[tuple[str, tuple[str, ...]], CanonicalRegionReference],
    target: RegionTarget,
) -> CanonicalRegionReference:
    try:
        return references[(target.object_uuid, target.region_path)]
    except KeyError as exc:
        raise CanonicalizationError("resolved relation refers to an unrepresented region") from exc


def canonicalize_ingest(result: ResolutionResult) -> CompletedIngest:
    """Create canonical records only from a fully valid resolution result."""

    if not result.is_valid:
        raise CanonicalizationError("cannot canonicalize an invalid resolution result")

    materialized = result.materialized_corpus
    parsed = materialized.parsed_corpus

    canonical_objects: list[CanonicalObject] = []
    canonical_regions: list[CanonicalRegion] = []
    region_references: dict[tuple[str, tuple[str, ...]], CanonicalRegionReference] = {}
    for note in parsed.notes:
        source_uuid = note.semantic_object.uuid
        if source_uuid is None:
            raise CanonicalizationError("cannot canonicalize an object without a UUID")
        object_regions = tuple(_canonical_region(source_uuid, region) for region in note.regions)
        canonical_objects.append(
            CanonicalObject(
                source_uuid,
                note.semantic_object.authored_path,
                note.semantic_object.path_hierarchy,
                note.semantic_object.admitted_fields,
                (),
                object_regions,
            )
        )
        canonical_regions.extend(object_regions)
        for region in object_regions:
            region_references[(source_uuid, region.reference.region_path)] = region.reference

    _verify_object_relation_provenance(materialized, result.resolved_object_relations)
    object_relations_by_uuid: dict[str, list[CanonicalObjectRelation]] = {}
    for relation in result.resolved_object_relations:
        object_relations_by_uuid.setdefault(relation.source_object_uuid, []).append(
            _canonical_object_relation(relation, region_references)
        )
    canonical_objects = [
        CanonicalObject(
            obj.source_object_uuid,
            obj.source_path,
            obj.path_hierarchy,
            obj.admitted_identifiers,
            tuple(object_relations_by_uuid.get(obj.source_object_uuid, ())),
            obj.regions,
        )
        for obj in canonical_objects
    ]

    resolved_relations = iter(result.resolved_relations)
    canonical_units: list[CanonicalUnit] = []
    for unit_id, materialized_unit in enumerate(materialized.units, start=1):
        unit_region_path = tuple(
            region_references[(materialized_unit.source_object_uuid, materialized_unit.region_path[:index])]
            for index in range(1, len(materialized_unit.region_path) + 1)
        )
        canonical_relation_values: list[CanonicalRelation] = []
        for authored_relation in materialized_unit.relations:
            try:
                resolved_relation = next(resolved_relations)
            except StopIteration as exc:
                raise CanonicalizationError("resolved relation ordering does not match materialized units") from exc
            if not _same_authored_occurrence(materialized_unit, authored_relation, resolved_relation):
                raise CanonicalizationError("resolved relation does not match its materialized authored occurrence")
            canonical_relation_values.append(_canonical_relation(resolved_relation, region_references))
        canonical_relations = tuple(canonical_relation_values)
        canonical_units.append(
            CanonicalUnit(
                unit_id,
                materialized_unit.source_object_uuid,
                materialized_unit.authored_path,
                materialized_unit.local_order,
                materialized_unit.path_hierarchy,
                unit_region_path,
                materialized_unit.raw_markdown,
                materialized_unit.parsed_text,
                materialized_unit.inherited_identifiers,
                canonical_relations,
                materialized_unit.embeds,
            )
        )
    try:
        next(resolved_relations)
    except StopIteration:
        pass
    else:
        raise CanonicalizationError("resolved relation ordering does not match materialized units")

    return CompletedIngest(
        tuple(canonical_objects),
        tuple(canonical_regions),
        tuple(canonical_units),
        parsed,
        materialized,
        result,
    )


def _verify_object_relation_provenance(
    materialized: MaterializedCorpus,
    resolved_relations: tuple[ResolvedObjectRelation, ...],
) -> None:
    """Require resolver output to preserve each authored object occurrence in order."""

    expected_by_uuid = {obj.source_object_uuid: obj for obj in materialized.objects}
    actual_by_uuid: dict[str, list[ResolvedObjectRelation]] = {
        source_uuid: [] for source_uuid in expected_by_uuid
    }
    for relation in resolved_relations:
        actual_by_uuid.setdefault(relation.source_object_uuid, []).append(relation)

    if set(expected_by_uuid) != set(actual_by_uuid):
        raise CanonicalizationError("resolved object relation sources do not match materialized objects")

    for source_uuid, materialized_object in expected_by_uuid.items():
        actual = actual_by_uuid[source_uuid]
        expected = materialized_object.relations
        if len(actual) != len(expected):
            raise CanonicalizationError(
                f"resolved object relation count does not match authored occurrences for {source_uuid!r}"
            )
        for authored, resolved in zip(expected, actual):
            if not _same_object_authored_occurrence(materialized_object, authored, resolved):
                raise CanonicalizationError(
                    f"resolved object relation provenance does not match authored occurrence for {source_uuid!r}"
                )


def _same_object_authored_occurrence(
    materialized_object: MaterializedObject,
    authored_relation: RelationCandidate,
    resolved_relation: ResolvedObjectRelation,
) -> bool:
    """Compare only authored provenance; destination fields remain resolver output."""

    return (
        resolved_relation.source_object_uuid == materialized_object.source_object_uuid
        and resolved_relation.source_path == materialized_object.authored_path
        and resolved_relation.relation_name == authored_relation.relation_name
        and resolved_relation.origin == authored_relation.origin
        and resolved_relation.source_field == authored_relation.source_field
        and resolved_relation.raw == authored_relation.raw
        and resolved_relation.authored_target == authored_relation.target
        and resolved_relation.authored_label == authored_relation.label
        and resolved_relation.authored_region_fragment == authored_relation.target_region_fragment
    )


def _same_authored_occurrence(
    materialized_unit: MaterializedUnit,
    authored_relation: RelationCandidate,
    resolved_relation: ResolvedRelation,
) -> bool:
    """Verify provenance before accepting resolver output at this position."""

    return (
        resolved_relation.source_object_uuid == materialized_unit.source_object_uuid
        and resolved_relation.source_path == materialized_unit.authored_path
        and resolved_relation.local_order == materialized_unit.local_order
        and resolved_relation.relation_name == authored_relation.relation_name
        and resolved_relation.origin == authored_relation.origin
        and resolved_relation.source_field == authored_relation.source_field
        and resolved_relation.raw == authored_relation.raw
        and resolved_relation.authored_target == authored_relation.target
        and resolved_relation.authored_label == authored_relation.label
        and resolved_relation.authored_region_fragment == authored_relation.target_region_fragment
    )


def _canonical_relation(
    relation: ResolvedRelation,
    region_references: dict[tuple[str, tuple[str, ...]], CanonicalRegionReference],
) -> CanonicalRelation:
    target_region = (
        _region_reference(region_references, relation.target_region)
        if relation.target_region is not None
        else None
    )
    return CanonicalRelation(
        relation.relation_name,
        relation.origin,
        relation.source_field,
        relation.raw,
        relation.authored_target,
        relation.authored_label,
        relation.authored_region_fragment,
        relation.source_object_uuid,
        relation.source_path,
        relation.local_order,
        relation.target_object_uuid,
        target_region,
    )


def _canonical_object_relation(
    relation: ResolvedObjectRelation,
    region_references: dict[tuple[str, tuple[str, ...]], CanonicalRegionReference],
) -> CanonicalObjectRelation:
    target_region = (
        _region_reference(region_references, relation.target_region)
        if relation.target_region is not None
        else None
    )
    return CanonicalObjectRelation(
        relation.relation_name,
        relation.origin,
        relation.source_field,
        relation.raw,
        relation.authored_target,
        relation.authored_label,
        relation.authored_region_fragment,
        relation.source_object_uuid,
        relation.source_path,
        relation.target_object_uuid,
        target_region,
    )
