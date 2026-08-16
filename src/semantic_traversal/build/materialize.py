"""Materialize object context and unresolved authored relation candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parser import (
    Embed,
    FrontmatterField,
    NoteParseError,
    Wikilink,
    _markdown_parser,
    _parsed_text_and_links,
)
from .vault import VaultParseResult


class MaterializationError(NoteParseError):
    """The parsed corpus cannot enter context materialization."""


@dataclass(frozen=True)
class RelationCandidate:
    """An authored, unresolved relation reason preserved for later resolution."""

    relation_name: str
    raw: str
    target: str
    label: str
    target_region_fragment: str | None
    origin: str  # frontmatter or body
    source_field: str | None


@dataclass(frozen=True)
class MaterializedObject:
    """Authored object state retained independently of semantic units."""

    source_object_uuid: str
    authored_path: str
    path_hierarchy: tuple[str, ...]
    admitted_identifiers: tuple[FrontmatterField, ...]
    relations: tuple[RelationCandidate, ...]


@dataclass(frozen=True)
class MaterializedUnit:
    """A parser unit with inherited object context and relation candidates."""

    local_order: int
    source_object_uuid: str
    authored_path: str
    path_hierarchy: tuple[str, ...]
    region_path: tuple[str, ...]
    raw_markdown: str
    parsed_text: str
    inherited_identifiers: tuple[FrontmatterField, ...]
    relations: tuple[RelationCandidate, ...]
    embeds: tuple[Embed, ...]


@dataclass(frozen=True)
class MaterializedCorpus:
    parsed_corpus: VaultParseResult
    units: tuple[MaterializedUnit, ...]
    objects: tuple[MaterializedObject, ...] = ()

    @property
    def build_config(self):
        """The exact configuration bound to the retained parsed corpus."""

        return self.parsed_corpus.build_config

    @property
    def represented_identifier_fields(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    field.name
                    for unit in self.units
                    for field in unit.inherited_identifiers
                    if field.state != "absent"
                }
            )
        )


def _frontmatter_links(value: Any) -> tuple[Wikilink, ...]:
    """Extract links only from the observed scalar/list-of-string shapes."""

    values: tuple[str, ...]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, list):
        values = tuple(item for item in value if isinstance(item, str))
    else:
        # Preserve arbitrary YAML values without coercing them into text.
        return ()

    parser = _markdown_parser()
    links: list[Wikilink] = []
    for authored_value in values:
        _, value_links, _ = _parsed_text_and_links(parser, authored_value)
        links.extend(value_links)
    return tuple(links)


def materialize_context(result: VaultParseResult) -> MaterializedCorpus:
    """Materialize only a valid parsed corpus; never promote invalid evidence."""

    if not result.is_valid:
        raise MaterializationError("cannot materialize context from an invalid parsed corpus")

    build_config = result.build_config

    materialized: list[MaterializedUnit] = []
    materialized_objects: list[MaterializedObject] = []
    for note in result.notes:
        source_uuid = note.semantic_object.uuid
        if source_uuid is None:
            raise MaterializationError("cannot materialize a semantic object without a UUID")

        frontmatter_relations: list[RelationCandidate] = []
        for field in note.semantic_object.admitted_fields:
            if field.name not in build_config.semantic_identifier_fields or field.state == "absent":
                continue
            for link in _frontmatter_links(note.semantic_object.frontmatter.get(field.name)):
                frontmatter_relations.append(
                    RelationCandidate(
                        relation_name=field.name,
                        raw=link.raw,
                        target=link.target,
                        label=link.label,
                        target_region_fragment=link.target_region_fragment,
                        origin="frontmatter",
                        source_field=field.name,
                    )
                )

        materialized_objects.append(
            MaterializedObject(
                source_uuid,
                note.semantic_object.authored_path,
                note.semantic_object.path_hierarchy,
                note.semantic_object.admitted_fields,
                tuple(frontmatter_relations),
            )
        )

        for unit in note.units:
            body_relations = tuple(
                RelationCandidate(
                    relation_name="linked_to",
                    raw=link.raw,
                    target=link.target,
                    label=link.label,
                    target_region_fragment=link.target_region_fragment,
                    origin="body",
                    source_field=None,
                )
                for link in unit.wikilinks
            )
            materialized.append(
                MaterializedUnit(
                    local_order=unit.local_order,
                    source_object_uuid=source_uuid,
                    authored_path=unit.authored_path,
                    path_hierarchy=unit.path_hierarchy,
                    region_path=unit.region_path,
                    raw_markdown=unit.raw_markdown,
                    parsed_text=unit.parsed_text,
                    inherited_identifiers=note.semantic_object.admitted_fields,
                    relations=tuple(frontmatter_relations) + body_relations,
                    embeds=unit.embeds,
                )
            )
    return MaterializedCorpus(result, tuple(materialized), tuple(materialized_objects))
