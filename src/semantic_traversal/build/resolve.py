"""Resolve authored relation candidates against one bound parsed corpus."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from .materialize import MaterializedCorpus, RelationCandidate
from .parser import HeadingRegion, NoteParseError


class ResolutionError(NoteParseError):
    """The resolution indexes or address shapes cannot be interpreted."""


@dataclass(frozen=True)
class ObjectTarget:
    object_uuid: str
    authored_path: str
    address: str
    name: str


@dataclass(frozen=True)
class RegionTarget:
    object_uuid: str
    heading: str
    region_path: tuple[str, ...]
    address_text: str


@dataclass(frozen=True)
class ResolvedRelation:
    relation_name: str
    origin: str
    source_field: str | None
    raw: str
    authored_target: str
    authored_label: str
    authored_region_fragment: str | None
    source_object_uuid: str
    source_path: str
    local_order: int
    target_object_uuid: str
    target_region: RegionTarget | None


@dataclass(frozen=True)
class ResolvedObjectRelation:
    """One resolved authored frontmatter occurrence at object scope."""

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
    target_region: RegionTarget | None


@dataclass(frozen=True)
class ResolutionFailure:
    kind: str
    message: str
    relation: RelationCandidate
    source_object_uuid: str
    source_path: str
    local_order: int | None
    object_candidates: tuple[ObjectTarget, ...] = ()
    region_candidates: tuple[RegionTarget, ...] = ()


@dataclass(frozen=True)
class ResolutionResult:
    materialized_corpus: MaterializedCorpus
    resolved_relations: tuple[ResolvedRelation, ...]
    failures: tuple[ResolutionFailure, ...]
    resolved_object_relations: tuple[ResolvedObjectRelation, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class _ResolutionIndex:
    by_address: dict[str, ObjectTarget]
    by_name_or_alias: dict[str, tuple[ObjectTarget, ...]]
    regions_by_uuid: dict[str, tuple[RegionTarget, ...]]


def _without_final_md(path: str) -> str:
    return path[:-3] if path.endswith(".md") else path


def _object_name(path: str) -> str:
    return _without_final_md(PurePosixPath(path).name)


def _object_address_key(value: str) -> str:
    """Normalize only name/alias comparison by the specified Unicode case-fold."""

    return value.casefold()


def _aliases(value: object, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ResolutionError(f"unsupported aliases value shape at {path}: {type(value).__name__}")


def _region_target(object_uuid: str, region: HeadingRegion) -> RegionTarget:
    return RegionTarget(object_uuid, region.heading, region.region_path + (region.region_id,), region.address_text)


def _build_index(corpus: MaterializedCorpus) -> _ResolutionIndex:
    parsed = corpus.parsed_corpus
    by_address: dict[str, ObjectTarget] = {}
    by_name: dict[str, list[ObjectTarget]] = defaultdict(list)
    regions_by_uuid: dict[str, tuple[RegionTarget, ...]] = {}
    for note in parsed.notes:
        semantic_object = note.semantic_object
        if semantic_object.uuid is None:
            raise ResolutionError("cannot index a semantic object without a UUID")
        target = ObjectTarget(
            semantic_object.uuid,
            semantic_object.authored_path,
            _without_final_md(semantic_object.authored_path),
            _object_name(semantic_object.authored_path),
        )
        if target.address in by_address:
            raise ResolutionError(f"duplicate authored note address: {target.address}")
        by_address[target.address] = target
        by_name[_object_address_key(target.name)].append(target)
        aliases_value = semantic_object.frontmatter.get("aliases") if "aliases" in parsed.build_config.semantic_identifier_fields else None
        for alias in _aliases(aliases_value, semantic_object.authored_path):
            by_name[_object_address_key(alias)].append(target)
        regions_by_uuid[target.object_uuid] = tuple(_region_target(target.object_uuid, region) for region in note.regions)

    deduplicated: dict[str, tuple[ObjectTarget, ...]] = {}
    for key, targets in by_name.items():
        unique = {target.object_uuid: target for target in targets}
        deduplicated[key] = tuple(sorted(unique.values(), key=lambda target: target.authored_path))
    return _ResolutionIndex(by_address, deduplicated, regions_by_uuid)


def _object_matches(target: str, index: _ResolutionIndex) -> tuple[ObjectTarget, ...]:
    if "/" in target:
        exact = index.by_address.get(target)
        return (exact,) if exact is not None else ()
    return index.by_name_or_alias.get(_object_address_key(target), ())


def _region_address_key(value: str) -> str:
    """Apply the specification's punctuation-insensitive address key only."""

    parts: list[str] = []
    current: list[str] = []
    for character in value:
        if character.isalnum():
            current.append(character)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return " ".join(parts).casefold()


def resolve_relations(corpus: MaterializedCorpus) -> ResolutionResult:
    """Resolve all relation candidates using only the corpus-bound state."""

    if not corpus.parsed_corpus.is_valid:
        raise ResolutionError("cannot resolve relations from an invalid parsed corpus")
    index = _build_index(corpus)
    resolved: list[ResolvedRelation] = []
    failures: list[ResolutionFailure] = []
    for unit in corpus.units:
        for relation in unit.relations:
            object_matches = _object_matches(relation.target, index)
            if not object_matches:
                failures.append(
                    ResolutionFailure(
                        "unresolved_object",
                        f"unresolved authored object target {relation.target!r}",
                        relation,
                        unit.source_object_uuid,
                        unit.authored_path,
                        unit.local_order,
                    )
                )
                continue
            if len(object_matches) > 1:
                failures.append(
                    ResolutionFailure(
                        "ambiguous_object",
                        f"ambiguous authored object target {relation.target!r}",
                        relation,
                        unit.source_object_uuid,
                        unit.authored_path,
                        unit.local_order,
                        object_candidates=object_matches,
                    )
                )
                continue

            object_target = object_matches[0]
            region_target: RegionTarget | None = None
            if relation.target_region_fragment is not None:
                region_matches = tuple(
                    region
                    for region in index.regions_by_uuid[object_target.object_uuid]
                    if region.address_text == relation.target_region_fragment
                )
                if not region_matches:
                    normalized_fragment = _region_address_key(relation.target_region_fragment)
                    region_matches = tuple(
                        region
                        for region in index.regions_by_uuid[object_target.object_uuid]
                        if _region_address_key(region.address_text) == normalized_fragment
                    )
                if not region_matches:
                    failures.append(
                        ResolutionFailure(
                            "unresolved_region",
                            f"unresolved authored region target {relation.target_region_fragment!r}",
                            relation,
                            unit.source_object_uuid,
                            unit.authored_path,
                            unit.local_order,
                        )
                    )
                    continue
                if len(region_matches) > 1:
                    failures.append(
                        ResolutionFailure(
                            "ambiguous_region",
                            f"ambiguous authored region target {relation.target_region_fragment!r}",
                            relation,
                            unit.source_object_uuid,
                            unit.authored_path,
                            unit.local_order,
                            region_candidates=region_matches,
                        )
                    )
                    continue
                region_target = region_matches[0]
            resolved.append(
                ResolvedRelation(
                    relation_name=relation.relation_name,
                    origin=relation.origin,
                    source_field=relation.source_field,
                    raw=relation.raw,
                    authored_target=relation.target,
                    authored_label=relation.label,
                    authored_region_fragment=relation.target_region_fragment,
                    source_object_uuid=unit.source_object_uuid,
                    source_path=unit.authored_path,
                    local_order=unit.local_order,
                    target_object_uuid=object_target.object_uuid,
                    target_region=region_target,
                )
            )
    object_resolved: list[ResolvedObjectRelation] = []
    for obj in corpus.objects:
        for relation in obj.relations:
            object_matches = _object_matches(relation.target, index)
            if not object_matches:
                failures.append(ResolutionFailure(
                    "unresolved_object", f"unresolved authored object target {relation.target!r}",
                    relation, obj.source_object_uuid, obj.authored_path, None,
                ))
                continue
            if len(object_matches) > 1:
                failures.append(ResolutionFailure(
                    "ambiguous_object", f"ambiguous authored object target {relation.target!r}",
                    relation, obj.source_object_uuid, obj.authored_path, None,
                    object_candidates=object_matches,
                ))
                continue
            object_target = object_matches[0]
            region_target: RegionTarget | None = None
            if relation.target_region_fragment is not None:
                region_matches = tuple(
                    region for region in index.regions_by_uuid[object_target.object_uuid]
                    if region.address_text == relation.target_region_fragment
                )
                if not region_matches:
                    normalized_fragment = _region_address_key(relation.target_region_fragment)
                    region_matches = tuple(
                        region for region in index.regions_by_uuid[object_target.object_uuid]
                        if _region_address_key(region.address_text) == normalized_fragment
                    )
                if not region_matches:
                    failures.append(ResolutionFailure(
                        "unresolved_region", f"unresolved authored region target {relation.target_region_fragment!r}",
                        relation, obj.source_object_uuid, obj.authored_path, None,
                    ))
                    continue
                if len(region_matches) > 1:
                    failures.append(ResolutionFailure(
                        "ambiguous_region", f"ambiguous authored region target {relation.target_region_fragment!r}",
                        relation, obj.source_object_uuid, obj.authored_path, None,
                        region_candidates=region_matches,
                    ))
                    continue
                region_target = region_matches[0]
            object_resolved.append(ResolvedObjectRelation(
                relation.relation_name, relation.origin, relation.source_field,
                relation.raw, relation.target, relation.label, relation.target_region_fragment,
                obj.source_object_uuid, obj.authored_path, object_target.object_uuid, region_target,
            ))
    return ResolutionResult(corpus, tuple(resolved), tuple(failures), tuple(object_resolved))
