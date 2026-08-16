"""Derivative field-dimensioned FTS5 lexical retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import sqlite3
from typing import Any, Iterable

from .substrate import SubstrateError, _decode_value


INTRINSIC_FIELD_CLASS = "intrinsic"
SEMANTIC_IDENTIFIER_FIELD_CLASS = "semantic_identifier"
REGION_FIELD_CLASS = "region"
SEMANTIC_PATH_FIELD_CLASS = "semantic_path"
REGION_TEXT_FIELD = "region_text"
PATH_COMPONENT_FIELD = "path_component"

_DIMENSION_TABLE_PREFIX = "lexical_fts_"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_TOKENIZER = "unicode61 remove_diacritics 0"


@dataclass(frozen=True)
class LexicalHit:
    """A lexical result containing only canonical retrieval identity and score."""

    unit_id: int
    score: float


def _table_name(field_class: str, field_name: str) -> str:
    digest = hashlib.sha256(f"{field_class}\0{field_name}".encode("utf-8")).hexdigest()[:24]
    return f"{_DIMENSION_TABLE_PREFIX}{digest}"


def _quoted_identifier(value: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise SubstrateError("invalid lexical implementation identifier")
    return f'"{value}"'


def _fts_literal(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _direct_text_members(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for member in value:
            if isinstance(member, str):
                yield member


def _create_dimension(connection: sqlite3.Connection, field_class: str, field_name: str) -> str:
    table_name = _table_name(field_class, field_name)
    quoted = _quoted_identifier(table_name)
    connection.execute(
        f"CREATE VIRTUAL TABLE {quoted} USING fts5(unit_id UNINDEXED, content, tokenize='{_TOKENIZER}')"
    )
    connection.execute(
        "INSERT INTO lexical_dimension_registry (field_class, field_name, table_name) VALUES (?, ?, ?)",
        (field_class, field_name, table_name),
    )
    return table_name


def _insert_occurrence(connection: sqlite3.Connection, table_name: str, unit_id: int, content: str) -> None:
    connection.execute(
        f"INSERT INTO {_quoted_identifier(table_name)} (unit_id, content) VALUES (?, ?)",
        (unit_id, content),
    )


def build_lexical_index(connection: sqlite3.Connection) -> None:
    """Derive isolated FTS5 populations solely from canonical SQLite tables."""

    try:
        connection.execute("CREATE VIRTUAL TABLE temp.lexical_fts_capability_check USING fts5(content)")
        connection.execute("DROP TABLE temp.lexical_fts_capability_check")
    except sqlite3.OperationalError as exc:
        raise SubstrateError("SQLite FTS5 is required for lexical retrieval") from exc

    with connection:
        old_tables = connection.execute(
            "SELECT table_name FROM lexical_dimension_registry"
        ).fetchall() if _table_exists(connection, "lexical_dimension_registry") else ()
        for (table_name,) in old_tables:
            connection.execute(f"DROP TABLE IF EXISTS {_quoted_identifier(table_name)}")
        connection.execute("DROP TABLE IF EXISTS lexical_dimension_registry")
        connection.execute(
            """
            CREATE TABLE lexical_dimension_registry (
                field_class TEXT NOT NULL,
                field_name TEXT NOT NULL,
                table_name TEXT NOT NULL UNIQUE,
                PRIMARY KEY (field_class, field_name)
            )
            """
        )

        units = connection.execute(
            "SELECT unit_id, parsed_text FROM canonical_units ORDER BY unit_id"
        ).fetchall()
        intrinsic = _create_dimension(connection, INTRINSIC_FIELD_CLASS, "parsed_text")
        for unit_id, parsed_text in units:
            _insert_occurrence(connection, intrinsic, unit_id, parsed_text)

        identifier_rows = connection.execute(
            """SELECT unit_id, field_name, state, value_json
            FROM inherited_identifiers ORDER BY unit_id, ordinal"""
        ).fetchall()
        identifier_tables: dict[str, str] = {}
        for unit_id, field_name, state, value_json in identifier_rows:
            if state != "present_value" or value_json is None:
                continue
            value = _decode_value(value_json)
            members = _direct_text_members(value)
            for member in members:
                table_name = identifier_tables.get(field_name)
                if table_name is None:
                    table_name = _create_dimension(connection, SEMANTIC_IDENTIFIER_FIELD_CLASS, field_name)
                    identifier_tables[field_name] = table_name
                _insert_occurrence(connection, table_name, unit_id, member)

        region_table: str | None = None
        region_rows = connection.execute(
            """SELECT p.unit_id, r.address_text
            FROM unit_region_path p
            JOIN canonical_regions r
              ON r.source_object_uuid = p.source_object_uuid
             AND r.region_path_json = p.region_path_json
            ORDER BY p.unit_id, p.ordinal"""
        ).fetchall()
        for unit_id, address_text in region_rows:
            if region_table is None:
                region_table = _create_dimension(connection, REGION_FIELD_CLASS, REGION_TEXT_FIELD)
            _insert_occurrence(connection, region_table, unit_id, address_text)

        path_table: str | None = None
        path_rows = connection.execute(
            "SELECT unit_id, component FROM unit_path_components ORDER BY unit_id, ordinal"
        ).fetchall()
        for unit_id, component in path_rows:
            if path_table is None:
                path_table = _create_dimension(connection, SEMANTIC_PATH_FIELD_CLASS, PATH_COMPONENT_FIELD)
            _insert_occurrence(connection, path_table, unit_id, component)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _dimension_table(connection: sqlite3.Connection, field_class: str, field_name: str) -> str:
    row = connection.execute(
        "SELECT table_name FROM lexical_dimension_registry WHERE field_class = ? AND field_name = ?",
        (field_class, field_name),
    ).fetchone()
    if row is None:
        raise KeyError(f"no lexical dimension: {field_class}/{field_name}")
    return row[0]


def _validate_terms_operands(connection: sqlite3.Connection, operands: tuple[str, ...]) -> None:
    """Require each public term operand to be one configured FTS token."""

    connection.execute("DROP TABLE IF EXISTS temp.lexical_terms_probe_vocab")
    connection.execute("DROP TABLE IF EXISTS temp.lexical_terms_probe")
    connection.execute(
        f"CREATE VIRTUAL TABLE temp.lexical_terms_probe USING fts5(content, tokenize='{_TOKENIZER}')"
    )
    connection.execute(
        "CREATE VIRTUAL TABLE temp.lexical_terms_probe_vocab USING fts5vocab(lexical_terms_probe, instance)"
    )
    try:
        for operand in operands:
            connection.execute("INSERT INTO temp.lexical_terms_probe(rowid, content) VALUES (1, ?)", (operand,))
            token_count = connection.execute(
                "SELECT COUNT(*) FROM temp.lexical_terms_probe_vocab WHERE doc = 1"
            ).fetchone()[0]
            connection.execute("DELETE FROM temp.lexical_terms_probe")
            if token_count != 1:
                raise ValueError(
                    f"terms operand must tokenize to exactly one lexical token: {operand!r} produced {token_count}"
                )
    finally:
        connection.execute("DROP TABLE IF EXISTS temp.lexical_terms_probe_vocab")
        connection.execute("DROP TABLE IF EXISTS temp.lexical_terms_probe")


def lexical_lookup(
    connection: sqlite3.Connection,
    field_class: str,
    field_name: str,
    operator: str,
    operand: Any,
) -> tuple[LexicalHit, ...]:
    """Run a structured terms or phrase query over one isolated FTS5 table."""

    table_name = _dimension_table(connection, field_class, field_name)
    if operator == "terms":
        if not isinstance(operand, (list, tuple)) or not operand or not all(isinstance(item, str) and item for item in operand):
            raise TypeError("terms requires a non-empty sequence of non-empty strings")
        _validate_terms_operands(connection, tuple(operand))
        match_expression = " OR ".join(_fts_literal(item) for item in operand)
    elif operator == "phrase":
        if not isinstance(operand, str) or not operand.strip():
            raise TypeError("phrase requires a non-empty string")
        match_expression = _fts_literal(operand)
    else:
        raise ValueError("lexical operator must be terms or phrase")

    quoted = _quoted_identifier(table_name)
    rows = connection.execute(
        f"""SELECT unit_id, bm25({quoted}) AS score FROM {quoted}
        WHERE {quoted} MATCH ? ORDER BY score ASC, unit_id ASC""",
        (match_expression,),
    ).fetchall()
    best: dict[int, float] = {}
    for unit_id, score in rows:
        best[unit_id] = min(best.get(unit_id, float("inf")), float(score))
    return tuple(LexicalHit(unit_id, score) for unit_id, score in sorted(best.items(), key=lambda item: (item[1], item[0])))


def lexical_integrity_check(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    """Run FTS5 integrity checks for every registered lexical dimension."""

    failures: list[tuple[Any, ...]] = []
    for (table_name,) in connection.execute("SELECT table_name FROM lexical_dimension_registry ORDER BY table_name"):
        quoted = _quoted_identifier(table_name)
        try:
            connection.execute(f"INSERT INTO {quoted}({quoted}) VALUES ('integrity-check')")
        except sqlite3.DatabaseError as exc:
            failures.append((table_name, str(exc)))
    return tuple(failures)
