"""Thin operator CLI over the accepted deterministic retrieval surfaces."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import dataclasses
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
from dotenv import load_dotenv

from .build.canonical import canonicalize_ingest
from .projection.catalog import generate_catalog
from .projection.capability_facts import human_capability_facts, observe_capability_facts
from .projection.exact import exact_lookup, build_exact_index
from .projection.graph import GraphHandle, build_graph, graph_discover, graph_relation_lookup, graph_traverse
from .projection.lexical import build_lexical_index, lexical_lookup
from .build.materialize import materialize_context
from .build.parser import _missing_semantic_identifier_descriptions, load_build_config
from .build.resolve import resolve_relations
from .projection.substrate import hydrate_object, hydrate_unit, write_completed_ingest
from .projection.temporal import after, before, between, earliest, latest, ordered, build_temporal_index
from .build.vault import parse_vault
from .projection.vector import OllamaEmbeddingProvider, build_vector_index, vector_eligible_targets, vector_lookup
from .projection.verification import verify_completed_build
from .runtime.config import load_runtime_config
from .runtime.retrieval.control_plane import conform_retrieval
from .runtime.conversation import SCHEMA_VERSION, append_message, create_conversation, get_conversation, initialize_runtime, migrate_runtime
from .runtime.router import route_conversation
from .runtime.retrieval.inference import infer_retrieval
from .runtime.turn import execute_turn


class CliError(ValueError):
    """An expected operator-facing failure."""


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return {"$type": type(value).__name__, "value": value.isoformat()}
    if dataclasses.is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _emit(value: Any, args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(_json_value(value), ensure_ascii=False, indent=2))
    elif isinstance(value, (list, tuple)):
        for item in value:
            print(json.dumps(_json_value(item), ensure_ascii=False))
    else:
        print(json.dumps(_json_value(value), ensure_ascii=False, indent=2))


def _connection(build: str, *, readonly: bool = True) -> sqlite3.Connection:
    path = Path(build).resolve() / "substrate.sqlite3"
    if not path.is_file():
        raise CliError(f"missing build artifact: {path}")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) if readonly else sqlite3.connect(path)


def _stage(name: str) -> None:
    print(name, file=sys.stderr)


def _build(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise CliError(f"output directory is not fresh and non-empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    db_path = output / "substrate.sqlite3"
    vector_path = output / "vectors.npy"
    try:
        _stage("config")
        config = load_build_config(args.config)
        _stage("parse")
        parsed = parse_vault(args.vault, config)
        if parsed.failures:
            raise CliError(json.dumps(_json_value(parsed.failures), ensure_ascii=False))
        print(f"parsed objects: {len(parsed.notes)}", file=sys.stderr)
        _stage("materialize")
        materialized = materialize_context(parsed)
        _stage("resolve")
        resolved = resolve_relations(materialized)
        if resolved.failures:
            raise CliError(json.dumps(_json_value(resolved.failures), ensure_ascii=False))
        _stage("canonical")
        completed = canonicalize_ingest(resolved)
        print(
            f"canonical objects: {len(completed.objects)}; regions: {len(completed.regions)}; units: {len(completed.units)}",
            file=sys.stderr,
        )
        _stage("substrate")
        connection = sqlite3.connect(db_path)
        try:
            write_completed_ingest(connection, completed)
            _stage("exact")
            build_exact_index(connection)
            print(f"exact entries: {connection.execute('SELECT COUNT(*) FROM exact_index_entries').fetchone()[0]}", file=sys.stderr)
            _stage("lexical")
            build_lexical_index(connection)
            lexical_tables = [row[0] for row in connection.execute("SELECT table_name FROM lexical_dimension_registry")]
            lexical_occurrences = sum(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in lexical_tables)
            print(f"lexical dimensions: {len(lexical_tables)}; occurrences: {lexical_occurrences}", file=sys.stderr)
            _stage("graph")
            build_graph(connection)
            print(
                f"graph nodes: {connection.execute('SELECT COUNT(*) FROM graph_nodes').fetchone()[0]}; "
                f"edges: {connection.execute('SELECT COUNT(*) FROM graph_edges').fetchone()[0]}; "
                f"relation types: {connection.execute('SELECT COUNT(*) FROM graph_relation_types').fetchone()[0]}",
                file=sys.stderr,
            )
            _stage("temporal")
            build_temporal_index(connection)
            print(f"temporal entries: {connection.execute('SELECT COUNT(*) FROM temporal_index_entries').fetchone()[0]}", file=sys.stderr)
            _stage("vector")
            targets = vector_eligible_targets(connection)
            provider = OllamaEmbeddingProvider(base_url=args.ollama_url)
            print(f"eligible vector targets: {len(targets)}", file=sys.stderr)
            print(f"requested embedding model: {provider.contract.requested_model}", file=sys.stderr)
            build_vector_index(connection, vector_path, provider)
            matrix = np.load(vector_path, allow_pickle=False)
            print(f"vector segments: {matrix.shape[0]}; matrix: {matrix.shape}; dtype: {matrix.dtype}", file=sys.stderr)
            print(f"resolved model identity: {provider.contract.resolved_model_identity}", file=sys.stderr)
            _stage("verify")
            _verify_connection(connection, vector_path)
        finally:
            connection.close()
        _stage("complete")
        result = {"substrate": str(db_path), "vectors": str(vector_path)}
        _emit(result, args)
        return result
    except Exception:
        for artifact in (db_path, vector_path):
            artifact.unlink(missing_ok=True)
        raise


def _verify_connection(connection: sqlite3.Connection, vector_path: Path) -> None:
    verify_completed_build(connection, vector_path)


def _inspect_artifacts(args: argparse.Namespace) -> None:
    root = Path(args.build).resolve()
    _emit({name: {"path": str(root / name), "exists": (root / name).is_file()} for name in ("substrate.sqlite3", "vectors.npy")}, args)


def _inspect_tables(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        _emit(tuple(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")), args)
    finally:
        connection.close()


def _inspect_counts(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        matrix = np.load(Path(args.build).resolve() / "vectors.npy", allow_pickle=False)
        lexical_tables = [row[0] for row in connection.execute("SELECT table_name FROM lexical_dimension_registry")]
        _emit({
            "canonical_objects": connection.execute("SELECT COUNT(*) FROM canonical_objects").fetchone()[0],
            "canonical_regions": connection.execute("SELECT COUNT(*) FROM canonical_regions").fetchone()[0],
            "canonical_units": connection.execute("SELECT COUNT(*) FROM canonical_units").fetchone()[0],
            "zero_unit_objects": connection.execute("SELECT COUNT(*) FROM canonical_objects o WHERE NOT EXISTS (SELECT 1 FROM canonical_units u WHERE u.source_object_uuid=o.source_object_uuid)").fetchone()[0],
            "empty_units": sum(
                not parsed_text.strip()
                for (parsed_text,) in connection.execute("SELECT parsed_text FROM canonical_units")
            ),
            "exact_entries": connection.execute("SELECT COUNT(*) FROM exact_index_entries").fetchone()[0],
            "temporal_entries": connection.execute("SELECT COUNT(*) FROM temporal_index_entries").fetchone()[0],
            "lexical_dimensions": len(lexical_tables),
            "lexical_occurrences": sum(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in lexical_tables),
            "graph_nodes": connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0],
            "graph_edges": connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0],
            "graph_relation_types": connection.execute("SELECT COUNT(*) FROM graph_relation_types").fetchone()[0],
            "vector_targets": connection.execute("SELECT COUNT(DISTINCT target_kind || ':' || target_identity_json) FROM vector_segments").fetchone()[0],
            "vector_segments": connection.execute("SELECT COUNT(*) FROM vector_segments").fetchone()[0],
            "matrix_rows": matrix.shape[0], "matrix_columns": matrix.shape[1], "matrix_dtype": str(matrix.dtype),
        }, args)
    finally:
        connection.close()


def _inspect_verify(args: argparse.Namespace) -> None:
    # The accepted integrity checks probe FTS dimensions with a temporary
    # sentinel row; use a normal connection for that bounded check and never
    # expose arbitrary write operations through the CLI.
    connection = _connection(args.build, readonly=False)
    try:
        _verify_connection(connection, Path(args.build).resolve() / "vectors.npy")
        _emit({"foreign_keys": 0, "lexical_integrity": 0, "graph_integrity": 0, "hydration": "ok", "vector": "ok"}, args)
    finally:
        connection.close()


def _inspect_capability_facts(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        facts = observe_capability_facts(connection, Path(args.build).resolve() / "vectors.npy")
        if args.json:
            _emit(facts, args)
        else:
            print(human_capability_facts(facts))
    finally:
        connection.close()


def _catalog_generate(args: argparse.Namespace) -> None:
    missing = _missing_semantic_identifier_descriptions(args.config)
    if missing:
        raise CliError(
            "missing authored semantic identifier descriptions:\n"
            + "\n".join(f"- {field_name}" for field_name in missing)
        )
    config = load_build_config(args.config)
    connection = _connection(args.build)
    try:
        facts = observe_capability_facts(connection, Path(args.build).resolve() / "vectors.npy")
    finally:
        connection.close()
    generate_catalog(facts, config.semantic_identifier_descriptions, args.output)
    _emit({"catalog": str(Path(args.output).resolve())}, args)


def _inspect_object(args: argparse.Namespace) -> None:
    if bool(args.path) == bool(args.uuid):
        raise CliError("exactly one of --path or --uuid is required")
    connection = _connection(args.build)
    try:
        uuid = args.uuid
        if args.path is not None:
            rows = connection.execute("SELECT source_object_uuid FROM canonical_objects WHERE source_path = ?", (args.path,)).fetchall()
            if len(rows) != 1:
                raise CliError(f"exact source path matched {len(rows)} objects")
            uuid = rows[0][0]
        _emit(hydrate_object(connection, uuid), args)
    finally:
        connection.close()


def _inspect_unit(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        _emit(hydrate_unit(connection, args.unit_id), args)
    finally:
        connection.close()


def _parse_scalar(value_type: str, value: str | None) -> Any:
    if value_type == "null":
        return None
    if value is None:
        raise CliError("--value is required for this value type")
    if value_type == "string": return value
    if value_type == "int": return int(value)
    if value_type == "float":
        parsed = float(value)
        if not np.isfinite(parsed): raise CliError("float must be finite")
        return parsed
    if value_type == "bool":
        if value not in {"true", "false"}: raise CliError("bool must be true or false")
        return value == "true"
    if value_type == "date": return dt.date.fromisoformat(value)
    if value_type == "datetime": return dt.datetime.fromisoformat(value)
    raise CliError(f"unknown value type: {value_type}")


def _exact(args: argparse.Namespace) -> None:
    if args.component and (args.value_type is not None or args.value is not None):
        raise CliError("--component cannot be combined with --value-type or --value")
    if args.component:
        value: Any = tuple(args.component)
    else:
        if args.value_type is None:
            raise CliError("exact lookup requires --component or --value-type")
        if args.value_type == "null" and args.value is not None:
            raise CliError("null exact lookup does not accept --value")
        if args.value_type != "null" and args.value is None:
            raise CliError("--value is required for this value type")
        value = _parse_scalar(args.value_type, args.value)
    connection = _connection(args.build)
    try:
        _emit(exact_lookup(connection, args.field_class, args.field_name, value), args)
    finally: connection.close()


def _lexical(args: argparse.Namespace) -> None:
    operand = tuple(args.term) if args.operator == "terms" else args.phrase
    connection = _connection(args.build)
    try: _emit(lexical_lookup(connection, args.field_class, args.field_name, args.operator, operand), args)
    finally: connection.close()


def _graph_discover(args: argparse.Namespace) -> None:
    operand = tuple(args.term) if args.operator == "terms" else args.phrase
    connection = _connection(args.build)
    try: _emit(graph_discover(connection, args.node_kind, args.dimension_name, args.operator, operand), args)
    finally: connection.close()


def _graph_relations(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try: _emit(graph_relation_lookup(connection, args.relation_class, args.relation_name), args)
    finally: connection.close()


def _graph_traverse(args: argparse.Namespace) -> None:
    raw = json.loads(args.handle_json)
    identity = raw["identity"]
    if raw["node_kind"] in {"semantic_object", "semantic_unit"}:
        identity = (identity[0],)
    elif raw["node_kind"] == "semantic_region":
        identity = (identity[0], tuple(identity[1]))
    elif raw["node_kind"] == "scope":
        identity = (tuple(identity[0]),)
    else:
        raise CliError("unknown graph handle node kind")
    handle = GraphHandle(raw["node_kind"], identity)
    connection = _connection(args.build)
    try: _emit(graph_traverse(connection, handle, args.relation_class, args.relation_name, args.direction), args)
    finally: connection.close()


def _vector(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        provider = OllamaEmbeddingProvider(base_url=args.ollama_url)
        _emit(vector_lookup(connection, Path(args.build).resolve() / "vectors.npy", args.query, provider), args)
    finally: connection.close()


def _parse_cli_date(value: str) -> dt.date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise CliError("date must use exact YYYY-MM-DD form")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CliError("date is not a valid calendar date") from exc


def _temporal(args: argparse.Namespace) -> None:
    connection = _connection(args.build)
    try:
        field = args.field
        if args.temporal_command == "earliest": result = earliest(connection, field)
        elif args.temporal_command == "latest": result = latest(connection, field)
        elif args.temporal_command == "before": result = before(connection, _parse_cli_date(args.anchor), field)
        elif args.temporal_command == "after": result = after(connection, _parse_cli_date(args.anchor), field)
        elif args.temporal_command == "between": result = between(connection, _parse_cli_date(args.start), _parse_cli_date(args.end), field)
        else: result = ordered(connection, args.direction, field)
        _emit(result, args)
    finally:
        connection.close()


def _runtime_init(args: argparse.Namespace) -> None:
    initialize_runtime(args.database)
    _emit({"database": str(Path(args.database).resolve()), "schema_version": SCHEMA_VERSION}, args)


def _runtime_migrate(args: argparse.Namespace) -> None:
    migrate_runtime(args.database)
    _emit({"database": str(Path(args.database).resolve()), "schema_version": SCHEMA_VERSION}, args)


def _runtime_conversation_create(args: argparse.Namespace) -> None:
    _emit(create_conversation(args.database), args)


def _runtime_message_append(args: argparse.Namespace) -> None:
    _emit(append_message(args.database, args.conversation_id, args.role, args.content), args)


def _runtime_conversation_show(args: argparse.Namespace) -> None:
    _emit(get_conversation(args.database, args.conversation_id), args)


def _load_model_execution_dotenv() -> None:
    dotenv_path = Path.cwd() / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)


def _runtime_router_infer(args: argparse.Namespace) -> None:
    config = load_runtime_config(args.config)
    _load_model_execution_dotenv()
    _emit(route_conversation(args.database, config, args.conversation_id), args)


def _runtime_control_plane_conform(args: argparse.Namespace) -> None:
    _emit(conform_retrieval(args.database, args.catalog, args.retrieval_run_id), args)

def _runtime_retrieval_infer(args: argparse.Namespace) -> None:
    config = load_runtime_config(args.config)
    _load_model_execution_dotenv()
    _emit(infer_retrieval(args.database, config, args.catalog, args.router_run_id), args)

def _runtime_turn_run(args: argparse.Namespace) -> None:
    config = load_runtime_config(args.config)
    _load_model_execution_dotenv()
    result = execute_turn(
        args.database, config, args.conversation_id,
        retrieval_build=args.build, ollama_url=args.ollama_url,
    )
    if args.json:
        coverage = result.packet_assembly.packet.coverage if result.packet_assembly is not None else None
        _emit({
            "conversation_id": result.conversation_id,
            "trigger_message_id": result.trigger_message_id,
            "route": result.route,
            "router_run_id": result.router_result.run_id,
            "retrieval_run_id": result.retrieval_inference_result.run_id if result.retrieval_inference_result else None,
            "conformance_id": result.conformance_result.conformance_id if result.conformance_result else None,
            "execution_id": result.execution_result.execution_id if result.execution_result else None,
            "retrieval_package_id": result.execution_result.retrieval_package_id if result.execution_result else None,
            "packet_selected_occurrences": coverage.selected_occurrences if coverage else None,
            "packet_omitted_occurrences": coverage.omitted_occurrences if coverage else None,
            "synthesis_run_id": result.synthesis_result.run_id,
            "produced_message_id": result.synthesis_result.produced_message_id,
            "response_text": result.synthesis_result.response_text,
        }, args)
    else:
        sys.stdout.write(result.synthesis_result.response_text or "")

def _add_build(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", required=True); parser.add_argument("--config", required=True); parser.add_argument("--output", required=True); parser.add_argument("--ollama-url", default="http://127.0.0.1:11434"); parser.add_argument("--json", action="store_true")


def _add_build_ref(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--build", required=True); parser.add_argument("--json", action="store_true")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="semantic-traversal"); sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build"); _add_build(b); b.set_defaults(handler=_build)
    inspect = sub.add_parser("inspect"); isub = inspect.add_subparsers(dest="inspect_command", required=True)
    a = isub.add_parser("artifacts"); _add_build_ref(a); a.set_defaults(handler=_inspect_artifacts)
    a = isub.add_parser("tables"); _add_build_ref(a); a.set_defaults(handler=_inspect_tables)
    a = isub.add_parser("counts"); _add_build_ref(a); a.set_defaults(handler=_inspect_counts)
    a = isub.add_parser("verify"); _add_build_ref(a); a.set_defaults(handler=_inspect_verify)
    a = isub.add_parser("capability-facts", help="observe diagnostic facts from a completed build"); _add_build_ref(a); a.set_defaults(handler=_inspect_capability_facts)
    a = isub.add_parser("object"); _add_build_ref(a); object_address = a.add_mutually_exclusive_group(required=True); object_address.add_argument("--path"); object_address.add_argument("--uuid"); a.set_defaults(handler=_inspect_object)
    a = isub.add_parser("unit"); _add_build_ref(a); a.add_argument("--unit-id", required=True, type=int); a.set_defaults(handler=_inspect_unit)
    catalog = sub.add_parser("catalog"); csub = catalog.add_subparsers(dest="catalog_command", required=True)
    a = csub.add_parser("generate", help="generate a model-facing capability catalog from accepted facts")
    _add_build_ref(a); a.add_argument("--config", required=True); a.add_argument("--output", required=True); a.set_defaults(handler=_catalog_generate)
    e = sub.add_parser("exact"); _add_build_ref(e); e.add_argument("--field-class", required=True); e.add_argument("--field-name", required=True); e.add_argument("--value-type", choices=["string","int","float","bool","date","datetime","null"]); e.add_argument("--value"); e.add_argument("--component", action="append"); e.set_defaults(handler=_exact)
    lexical = sub.add_parser("lexical"); lsub = lexical.add_subparsers(dest="operator", required=True)
    for op in ("terms", "phrase"):
        a = lsub.add_parser(op); _add_build_ref(a); a.add_argument("--field-class", required=True); a.add_argument("--field-name", required=True); (a.add_argument("--term", action="append", required=True) if op == "terms" else a.add_argument("--phrase", required=True)); a.set_defaults(handler=_lexical)
    graph = sub.add_parser("graph"); gsub = graph.add_subparsers(dest="graph_command", required=True)
    discover = gsub.add_parser("discover"); dsub = discover.add_subparsers(dest="operator", required=True)
    for op in ("terms", "phrase"):
        a = dsub.add_parser(op); _add_build_ref(a); a.add_argument("--node-kind", required=True); a.add_argument("--dimension-name", required=True); (a.add_argument("--term", action="append", required=True) if op == "terms" else a.add_argument("--phrase", required=True)); a.set_defaults(handler=_graph_discover)
    a = gsub.add_parser("relations"); _add_build_ref(a); a.add_argument("--relation-class", required=True); a.add_argument("--relation-name", required=True); a.set_defaults(handler=_graph_relations)
    a = gsub.add_parser("traverse"); _add_build_ref(a); a.add_argument("--handle-json", required=True); a.add_argument("--relation-class", required=True); a.add_argument("--relation-name", required=True); a.add_argument("--direction", choices=["inbound","outbound"], required=True); a.set_defaults(handler=_graph_traverse)
    v = sub.add_parser("vector"); _add_build_ref(v); v.add_argument("--query", required=True); v.add_argument("--ollama-url", default="http://127.0.0.1:11434"); v.set_defaults(handler=_vector)
    temporal = sub.add_parser("temporal"); tsub = temporal.add_subparsers(dest="temporal_command", required=True)
    a = tsub.add_parser("earliest"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.set_defaults(handler=_temporal)
    a = tsub.add_parser("latest"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.set_defaults(handler=_temporal)
    a = tsub.add_parser("before"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.add_argument("--anchor", required=True); a.set_defaults(handler=_temporal)
    a = tsub.add_parser("after"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.add_argument("--anchor", required=True); a.set_defaults(handler=_temporal)
    a = tsub.add_parser("between"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.add_argument("--start", required=True); a.add_argument("--end", required=True); a.set_defaults(handler=_temporal)
    a = tsub.add_parser("ordered"); _add_build_ref(a); a.add_argument("--field", default="journal_entry_date"); a.add_argument("--direction", choices=["ascending", "descending"], required=True); a.set_defaults(handler=_temporal)
    runtime = sub.add_parser("runtime"); rsub = runtime.add_subparsers(dest="runtime_command", required=True)
    a = rsub.add_parser("init"); a.add_argument("--database", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_init)
    a = rsub.add_parser("migrate"); a.add_argument("--database", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_migrate)
    conversation = rsub.add_parser("conversation"); csub = conversation.add_subparsers(dest="conversation_command", required=True)
    a = csub.add_parser("create"); a.add_argument("--database", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_conversation_create)
    a = csub.add_parser("show"); a.add_argument("--database", required=True); a.add_argument("--conversation-id", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_conversation_show)
    message = rsub.add_parser("message"); msub = message.add_subparsers(dest="message_command", required=True)
    a = msub.add_parser("append"); a.add_argument("--database", required=True); a.add_argument("--conversation-id", required=True); a.add_argument("--role", choices=["user", "synthesis"], required=True); a.add_argument("--content", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_message_append)
    router = rsub.add_parser("router"); rrouter = router.add_subparsers(dest="router_command", required=True)
    a = rrouter.add_parser("infer"); a.add_argument("--database", required=True); a.add_argument("--config", required=True); a.add_argument("--conversation-id", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_router_infer)
    retrieval = rsub.add_parser("retrieval-inference"); rretrieval = retrieval.add_subparsers(dest="retrieval_command", required=True)
    a = rretrieval.add_parser("infer"); a.add_argument("--database", required=True); a.add_argument("--config", required=True); a.add_argument("--catalog", required=True); a.add_argument("--router-run-id", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_retrieval_infer)
    turn = rsub.add_parser("turn"); tturn = turn.add_subparsers(dest="turn_command", required=True)
    a = tturn.add_parser("run"); a.add_argument("--database", required=True); a.add_argument("--config", required=True); a.add_argument("--build", required=True); a.add_argument("--conversation-id", required=True); a.add_argument("--ollama-url", default="http://127.0.0.1:11434"); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_turn_run)
    control_plane = rsub.add_parser("control-plane"); cpsub = control_plane.add_subparsers(dest="control_plane_command", required=True)
    a = cpsub.add_parser("conform"); a.add_argument("--database", required=True); a.add_argument("--catalog", required=True); a.add_argument("--retrieval-run-id", required=True); a.add_argument("--json", action="store_true"); a.set_defaults(handler=_runtime_control_plane_conform)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
        return 0
    except (CliError, KeyError, TypeError, ValueError, sqlite3.Error, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
