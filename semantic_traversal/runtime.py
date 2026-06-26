from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hashing import sha256_json, sha256_text
from .llm import LLMBackend
from .storage import append_ledger_record, create_thread_paths, load_json, read_ledger, write_json


@dataclass(frozen=True)
class TurnExecutionResult:
    thread_id: str
    turn_id: int
    thread_root: Path
    turn_root: Path
    conversation_thread_path: Path
    thread_state_path: Path
    thread_ledger_path: Path
    semantic_context_packet_path: Path
    semantic_traversal_manifest_path: Path
    retrieval_packet_path: Path
    coverage_report_path: Path
    synthesis_context_packet_path: Path
    state_delta_path: Path
    assistant_response: str
    llm_metadata: dict[str, Any]
    prior_thread_state: dict[str, Any]
    next_thread_state: dict[str, Any]
    ledger_record: dict[str, Any]
    semantic_context_packet: dict[str, Any]
    semantic_traversal_manifest: dict[str, Any]
    retrieval_packet: dict[str, Any]
    coverage_report: dict[str, Any]
    synthesis_context_packet: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "our",
    "the",
    "to",
    "with",
    "you",
    "your",
}
INSTRUCTION_TERMS = {
    "answer",
    "find",
    "help",
    "look",
    "note",
    "notes",
    "please",
    "prompt",
    "query",
    "recommend",
    "retrieve",
    "retrieval",
    "search",
    "show",
    "tell",
    "test",
    "testing",
    "use",
    "using",
    "want",
    "wanted",
}
WEAK_QUESTION_TERMS = {
    "day",
    "how",
    "often",
    "where",
    "what",
    "when",
    "which",
    "who",
    "why",
    "time",
    "usually",
    "usual",
}
SUPPORT_TERMS = {
    "buy",
    "call",
    "eat",
    "follow",
    "go",
    "meet",
    "read",
    "see",
    "visit",
    "work",
    "write",
    "dream",
    "call",
    "keep",
    "make",
    "before",
    "after",
    "with",
    "about",
}
QUERY_ROLE_WEIGHTS = {
    "anchor_exact": 8,
    "anchor_substring": 5,
    "support_exact": 4,
    "support_substring": 2,
    "weak_exact": 1,
    "weak_substring": 0,
    "ignored": 0,
}
RETRIEVAL_LIMIT = 6


def _default_thread_document(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "created_at": created_at,
        "updated_at": created_at,
        "turn_count": 0,
        "latest_thread_state_hash": None,
        "latest_perturbation_hash": None,
        "ledger_record_count": 0,
        "messages": [],
    }


def _default_thread_state(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "latest_turn_id": 0,
        "conversation_summary": "",
        "recent_messages": [],
        "current_user_goals": [],
        "open_questions": [],
        "active_constraints": [],
        "recent_semantic_trajectory": [],
        "latest_user_input": None,
        "latest_assistant_response": None,
        "updated_at": created_at,
        "latest_thread_state_hash": None,
    }


def _build_synthesis_context_packet(
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
    semantic_context_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "prior_thread_state": prior_thread_state,
        "visible_transcript_tail": thread_document["messages"][-6:],
        "semantic_context_packet": semantic_context_packet,
        "semantic_traversal_manifest": semantic_traversal_manifest,
        "retrieval_packet": retrieval_packet,
        "approved_retrieval_packet": retrieval_packet if coverage_report.get("retrieval_approved_for_synthesis") else None,
        "coverage_report": coverage_report,
        "output_requirements": [
            "Respond directly to the latest user input.",
            "Preserve continuity with the prior thread state.",
            "Use retrieved material only when present and relevant.",
            "Do not invent retrieval results.",
            "State retrieval limits if the retrieval packet is empty or partial.",
        ],
    }


def _build_semantic_context_packet(
    *,
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
) -> dict[str, Any]:
    query_analysis = _analyze_lexical_query(user_input)
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "extracted_lexical_query_terms": list(query_analysis["all_extracted_terms"]),
        "query_analysis": query_analysis,
        "prior_thread_state_context": {
            "latest_turn_id": prior_thread_state.get("latest_turn_id", 0),
            "conversation_summary": prior_thread_state.get("conversation_summary", ""),
            "recent_semantic_trajectory": list(prior_thread_state.get("recent_semantic_trajectory") or []),
            "recent_messages": list(prior_thread_state.get("recent_messages") or [])[-4:],
        },
        "explicit_limitation": "lexical/deterministic extraction only; no pre-call LLM extraction",
    }


def _build_lexical_retrieval_artifacts(
    *,
    repo_root: Path,
    data_root: Path,
    semantic_context_packet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    query_analysis = dict(semantic_context_packet.get("query_analysis") or {})
    query_terms = list(query_analysis.get("query_terms_for_retrieval") or [])
    database_path = (data_root / "ingestion" / "latent_space.sqlite3").resolve()
    traversal_manifest: dict[str, Any] = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "query_terms": query_terms,
        "query_terms_available": bool(query_terms),
        "retrieval_mode": "lexical_sqlite",
        "selected_chunk_ids": [],
        "candidate_count": 0,
        "selection_reasons": [],
        "limitations": [
            "no vector search",
            "no graph expansion",
            "no coverage loop",
            "lexical_sqlite only",
            "deterministic lexical query analysis only",
        ],
        "database_path": str(database_path),
        "repo_root": str(repo_root),
        "query_analysis": query_analysis,
    }
    retrieval_packet: dict[str, Any] = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "query_terms": query_terms,
        "query_terms_available": bool(query_terms),
        "retrieval_mode": "lexical_sqlite",
        "selection_limit": RETRIEVAL_LIMIT,
        "candidate_count": 0,
        "matched_chunk_count": 0,
        "approved_for_synthesis": False,
        "retrieval_status": "not_attempted",
        "selected_chunks": [],
        "database_path": str(database_path),
        "query_analysis": query_analysis,
    }
    coverage_report: dict[str, Any] = {
        "status": "not_attempted",
        "matched_chunk_count": 0,
        "candidate_count": 0,
        "query_terms_used": query_terms,
        "query_intent": query_analysis.get("query_intent"),
        "limits": {
            "selection_limit": RETRIEVAL_LIMIT,
        },
        "retrieval_approved_for_synthesis": False,
    }

    if not database_path.exists():
        traversal_manifest["selection_reasons"].append("ingestion SQLite database not found")
        coverage_report["status"] = "no_index"
        retrieval_packet["retrieval_status"] = "no_index"
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    if not query_terms:
        traversal_manifest["selection_reasons"].append("no usable lexical query terms after query analysis")
        coverage_report["status"] = "no_query_terms"
        retrieval_packet["retrieval_status"] = "no_query_terms"
        coverage_report["retrieval_approved_for_synthesis"] = False
        retrieval_packet["approved_for_synthesis"] = False
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        candidates = _query_lexical_candidates(connection, query_terms=query_terms)
    finally:
        connection.close()

    traversal_manifest["candidate_count"] = len(candidates)
    retrieval_packet["candidate_count"] = len(candidates)
    coverage_report["candidate_count"] = len(candidates)

    if not candidates:
        traversal_manifest["selection_reasons"].append("no chunk text or metadata matched the lexical query terms")
        coverage_report["status"] = "no_matches"
        retrieval_packet["retrieval_status"] = "no_matches"
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    selected_chunks = candidates[:RETRIEVAL_LIMIT]
    traversal_manifest["selected_chunk_ids"] = [chunk["chunk_id"] for chunk in selected_chunks]
    traversal_manifest["selection_reasons"] = [chunk["selection_reason"] for chunk in selected_chunks]
    has_anchor_or_support = any(chunk["matched_anchor_terms"] or chunk["matched_support_terms"] for chunk in selected_chunks)
    coverage_report["status"] = "minimal_pass" if has_anchor_or_support else "weak_lexical_match"
    coverage_report["matched_chunk_count"] = len(selected_chunks)
    coverage_report["retrieval_approved_for_synthesis"] = has_anchor_or_support
    retrieval_packet["matched_chunk_count"] = len(selected_chunks)
    retrieval_packet["approved_for_synthesis"] = has_anchor_or_support
    retrieval_packet["retrieval_status"] = coverage_report["status"]
    retrieval_packet["selected_chunks"] = selected_chunks
    return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
        traversal_manifest, retrieval_packet, coverage_report
    )


def _chunkless_retrieval_hashes(
    traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
) -> dict[str, str]:
    return {
        "semantic_traversal_manifest_hash": sha256_json(traversal_manifest),
        "retrieval_packet_hash": sha256_json(retrieval_packet),
        "coverage_report_hash": sha256_json(coverage_report),
    }


def _query_lexical_candidates(connection: sqlite3.Connection, *, query_terms: list[str]) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[str] = []
    for term in query_terms:
        like_term = f"%{term}%"
        term_clause = []
        for field in ("paragraph_text", "note_title", "section_label", "relative_path", "note_path"):
            term_clause.append(f"lower({field}) LIKE ?")
            parameters.append(like_term)
        clauses.append("(" + " OR ".join(term_clause) + ")")

    sql = (
        "SELECT chunk_id, note_id, source_root_label, relative_path, note_path, note_title, "
        "section_id, section_label, section_path_json, paragraph_ordinal, paragraph_text, chunk_hash "
        "FROM chunks WHERE "
        + " OR ".join(clauses)
    )
    rows = connection.execute(sql, tuple(parameters)).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        searchable_text = " ".join(
            [
                str(row["note_title"]),
                str(row["section_label"]),
                str(row["relative_path"]),
                str(row["paragraph_text"]),
            ]
        ).lower()
        selection = _score_lexical_candidate(searchable_text=searchable_text, query_terms=query_terms)
        if selection["selection_score"] <= 0:
            continue
        section_path = json.loads(row["section_path_json"]) if row["section_path_json"] else []
        candidates.append(
            {
                "chunk_id": row["chunk_id"],
                "note_id": row["note_id"],
                "note_title": row["note_title"],
                "relative_path": row["relative_path"],
                "section_id": row["section_id"],
                "section_label": row["section_label"],
                "section_path": section_path,
                "paragraph_ordinal": row["paragraph_ordinal"],
                "paragraph_text": row["paragraph_text"],
                "chunk_hash": row["chunk_hash"],
                "source_root_label": row["source_root_label"],
                **selection,
            }
        )
    candidates.sort(
        key=lambda item: (
            -item["selection_score"],
            -len(item["matched_anchor_terms"]),
            -len(item["matched_support_terms"]),
            -len(item["matched_weak_terms"]),
            item["chunk_id"],
        )
    )
    return candidates


def _score_lexical_candidate(*, searchable_text: str, query_terms: list[str]) -> dict[str, Any]:
    tokens = QUERY_TOKEN_RE.findall(searchable_text)
    token_set = set(tokens)
    selection_score = 0
    score_breakdown = {
        "anchor_exact": 0,
        "anchor_substring": 0,
        "support_exact": 0,
        "support_substring": 0,
        "weak_exact": 0,
        "weak_substring": 0,
        "ignored": 0,
    }
    matched_anchor_terms: list[str] = []
    matched_support_terms: list[str] = []
    matched_weak_terms: list[str] = []
    matched_ignored_terms: list[str] = []
    unmatched_anchor_terms: list[str] = []
    for term in query_terms:
        role = _lexical_query_role(term)
        exact_match = term in token_set
        substring_match = not exact_match and len(term) >= 4 and term in searchable_text
        if role == "anchor":
            if exact_match:
                selection_score += QUERY_ROLE_WEIGHTS["anchor_exact"]
                score_breakdown["anchor_exact"] += QUERY_ROLE_WEIGHTS["anchor_exact"]
                matched_anchor_terms.append(term)
            elif substring_match:
                selection_score += QUERY_ROLE_WEIGHTS["anchor_substring"]
                score_breakdown["anchor_substring"] += QUERY_ROLE_WEIGHTS["anchor_substring"]
                matched_anchor_terms.append(term)
            else:
                unmatched_anchor_terms.append(term)
        elif role == "support":
            if exact_match:
                selection_score += QUERY_ROLE_WEIGHTS["support_exact"]
                score_breakdown["support_exact"] += QUERY_ROLE_WEIGHTS["support_exact"]
                matched_support_terms.append(term)
            elif substring_match:
                selection_score += QUERY_ROLE_WEIGHTS["support_substring"]
                score_breakdown["support_substring"] += QUERY_ROLE_WEIGHTS["support_substring"]
                matched_support_terms.append(term)
        elif role == "weak":
            if exact_match:
                selection_score += QUERY_ROLE_WEIGHTS["weak_exact"]
                score_breakdown["weak_exact"] += QUERY_ROLE_WEIGHTS["weak_exact"]
                matched_weak_terms.append(term)
            elif substring_match:
                selection_score += QUERY_ROLE_WEIGHTS["weak_substring"]
                score_breakdown["weak_substring"] += QUERY_ROLE_WEIGHTS["weak_substring"]
                matched_weak_terms.append(term)
        else:
            if exact_match or substring_match:
                matched_ignored_terms.append(term)
                score_breakdown["ignored"] += QUERY_ROLE_WEIGHTS["ignored"]
    matched_terms = matched_anchor_terms + matched_support_terms + matched_weak_terms + matched_ignored_terms
    return {
        "selection_score": selection_score,
        "score_breakdown": score_breakdown,
        "matched_terms": matched_terms,
        "matched_anchor_terms": matched_anchor_terms,
        "matched_support_terms": matched_support_terms,
        "matched_weak_terms": matched_weak_terms,
        "matched_ignored_terms": matched_ignored_terms,
        "unmatched_anchor_terms": unmatched_anchor_terms,
        "selection_reason": _format_selection_reason(
            matched_anchor_terms=matched_anchor_terms,
            matched_support_terms=matched_support_terms,
            matched_weak_terms=matched_weak_terms,
            unmatched_anchor_terms=unmatched_anchor_terms,
            score_breakdown=score_breakdown,
        ),
    }


def _format_selection_reason(
    *,
    matched_anchor_terms: list[str],
    matched_support_terms: list[str],
    matched_weak_terms: list[str],
    unmatched_anchor_terms: list[str],
    score_breakdown: dict[str, int],
) -> str:
    evidence: list[str] = []
    if matched_anchor_terms:
        evidence.append(f"anchor={', '.join(matched_anchor_terms)}")
    if matched_support_terms:
        evidence.append(f"support={', '.join(matched_support_terms)}")
    if matched_weak_terms:
        evidence.append(f"weak={', '.join(matched_weak_terms)}")
    if not evidence:
        evidence.append("no lexical evidence")
    if unmatched_anchor_terms:
        evidence.append(f"unmatched_anchor={', '.join(unmatched_anchor_terms)}")
    score_bits = ", ".join(f"{name}:{value}" for name, value in score_breakdown.items() if value)
    if score_bits:
        evidence.append(f"score={score_bits}")
    return "; ".join(evidence)


def _lexical_query_role(term: str) -> str:
    if term in INSTRUCTION_TERMS:
        return "ignored"
    if term in SUPPORT_TERMS:
        return "support"
    if term in WEAK_QUESTION_TERMS:
        return "weak"
    return "anchor"


def _analyze_lexical_query(user_input: str) -> dict[str, Any]:
    all_extracted_terms = _extract_lexical_query_terms(user_input)
    quoted_terms = _extract_quoted_lexical_terms(user_input)
    ignored_instruction_terms: list[str] = []
    weak_question_terms: list[str] = []
    anchor_terms: list[str] = []
    support_terms: list[str] = []
    seen: set[str] = set()
    for term in all_extracted_terms:
        if term in seen:
            continue
        seen.add(term)
        if term in quoted_terms:
            anchor_terms.append(term)
            continue
        role = _lexical_query_role(term)
        if role == "ignored":
            ignored_instruction_terms.append(term)
        elif role == "weak":
            weak_question_terms.append(term)
        elif role == "support":
            support_terms.append(term)
        else:
            anchor_terms.append(term)
    query_terms_for_retrieval: list[str] = []
    for term in anchor_terms + support_terms + weak_question_terms:
        if term not in query_terms_for_retrieval:
            query_terms_for_retrieval.append(term)
    query_intent = _infer_query_intent(
        user_input=user_input,
        anchor_terms=anchor_terms,
        support_terms=support_terms,
        weak_question_terms=weak_question_terms,
        ignored_instruction_terms=ignored_instruction_terms,
        all_extracted_terms=all_extracted_terms,
    )
    return {
        "raw_user_input": user_input,
        "all_extracted_terms": all_extracted_terms,
        "ignored_instruction_terms": ignored_instruction_terms,
        "weak_question_terms": weak_question_terms,
        "anchor_terms": anchor_terms,
        "support_terms": support_terms,
        "query_terms_for_retrieval": query_terms_for_retrieval,
        "query_intent": query_intent,
        "limitations": [
            "deterministic lexical query analysis only",
            "no embeddings",
            "no graph traversal",
            "no LLM pre-call extraction",
        ],
    }


def _extract_quoted_lexical_terms(user_input: str) -> list[str]:
    quoted_terms: list[str] = []
    for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', user_input):
        span = match.group(1) or match.group(2) or ""
        for token in QUERY_TOKEN_RE.findall(span.lower()):
            if len(token) < 3 or token in STOP_WORDS or token.isdigit():
                continue
            if token not in quoted_terms:
                quoted_terms.append(token)
    return quoted_terms


def _infer_query_intent(
    *,
    user_input: str,
    anchor_terms: list[str],
    support_terms: list[str],
    weak_question_terms: list[str],
    ignored_instruction_terms: list[str],
    all_extracted_terms: list[str],
) -> str:
    lower_input = user_input.lower()
    if not all_extracted_terms:
        return "unknown_lexical_query"
    if any(term in {"continue", "follow", "resume"} for term in all_extracted_terms):
        return "continue_thread_with_retrieval"
    if anchor_terms or support_terms:
        return "answer_question_from_retrieved_notes" if weak_question_terms or "?" in lower_input else "retrieve_related_notes"
    if weak_question_terms and not ignored_instruction_terms:
        return "answer_question_from_retrieved_notes"
    return "unknown_lexical_query"


def _extract_lexical_query_terms(user_input: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(user_input.lower()):
        if len(token) < 3 or token in STOP_WORDS:
            continue
        if token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _persist_turn_artifacts(
    *,
    turn_root: Path,
    semantic_context_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
    synthesis_context_packet: dict[str, Any],
    state_delta: dict[str, Any],
) -> dict[str, Path]:
    turn_root.mkdir(parents=True, exist_ok=True)
    semantic_context_packet_path = turn_root / "semantic_context_packet.json"
    semantic_traversal_manifest_path = turn_root / "semantic_traversal_manifest.json"
    retrieval_packet_path = turn_root / "retrieval_packet.json"
    coverage_report_path = turn_root / "coverage_report.json"
    synthesis_context_packet_path = turn_root / "synthesis_context_packet.json"
    state_delta_path = turn_root / "state_delta.json"
    write_json(semantic_context_packet_path, semantic_context_packet)
    write_json(semantic_traversal_manifest_path, semantic_traversal_manifest)
    write_json(retrieval_packet_path, retrieval_packet)
    write_json(coverage_report_path, coverage_report)
    write_json(synthesis_context_packet_path, synthesis_context_packet)
    write_json(state_delta_path, state_delta)
    return {
        "semantic_context_packet_path": semantic_context_packet_path,
        "semantic_traversal_manifest_path": semantic_traversal_manifest_path,
        "retrieval_packet_path": retrieval_packet_path,
        "coverage_report_path": coverage_report_path,
        "synthesis_context_packet_path": synthesis_context_packet_path,
        "state_delta_path": state_delta_path,
    }


def _project_next_thread_state(
    thread_id: str,
    prior_thread_state: dict[str, Any],
    user_input: str,
    assistant_response: str,
    turn_id: int,
    timestamp: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior_recent_messages = list(prior_thread_state.get("recent_messages") or [])
    updated_recent_messages = (
        prior_recent_messages
        + [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_response},
        ]
    )[-6:]
    next_thread_state = {
        "thread_id": thread_id,
        "latest_turn_id": turn_id,
        "conversation_summary": assistant_response,
        "recent_messages": updated_recent_messages,
        "current_user_goals": [user_input],
        "open_questions": list(prior_thread_state.get("open_questions") or []),
        "active_constraints": list(prior_thread_state.get("active_constraints") or []),
        "recent_semantic_trajectory": [message["content"] for message in updated_recent_messages[-4:]],
        "latest_user_input": user_input,
        "latest_assistant_response": assistant_response,
        "updated_at": timestamp,
    }
    next_thread_state_hash = sha256_json(next_thread_state)
    next_thread_state["latest_thread_state_hash"] = next_thread_state_hash
    state_delta = {
        "from_turn_id": prior_thread_state.get("latest_turn_id", 0),
        "to_turn_id": turn_id,
        "latest_user_input": user_input,
        "latest_assistant_response": assistant_response,
        "latest_thread_state_hash": next_thread_state_hash,
    }
    return next_thread_state, state_delta


def run_thread_turn(
    *,
    repo_root: Path,
    data_root: Path,
    user_input: str,
    llm_backend: LLMBackend,
    thread_id: str | None = None,
) -> TurnExecutionResult:
    timestamp = _utc_now()
    paths = create_thread_paths(data_root=data_root, thread_id=thread_id)

    thread_document = load_json(paths.conversation_thread_path) or _default_thread_document(paths.thread_id, timestamp)
    prior_thread_state = load_json(paths.thread_state_path) or _default_thread_state(paths.thread_id, timestamp)
    ledger_records = read_ledger(paths.thread_ledger_path)
    turn_id = len(ledger_records) + 1
    parent_perturbation_hash = ledger_records[-1]["state_perturbation_hash"] if ledger_records else None
    prior_thread_state_hash = prior_thread_state.get("latest_thread_state_hash")
    turn_root = paths.turn_root(turn_id)

    semantic_context_packet = _build_semantic_context_packet(
        thread_document=thread_document,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        turn_id=turn_id,
    )
    semantic_traversal_manifest, retrieval_packet, coverage_report, retrieval_hashes = _build_lexical_retrieval_artifacts(
        repo_root=repo_root,
        data_root=data_root,
        semantic_context_packet=semantic_context_packet,
    )

    synthesis_context_packet = _build_synthesis_context_packet(
        thread_document=thread_document,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        turn_id=turn_id,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
    )
    semantic_context_packet_hash = sha256_json(semantic_context_packet)
    semantic_traversal_manifest_hash = retrieval_hashes["semantic_traversal_manifest_hash"]
    retrieval_packet_hash = retrieval_hashes["retrieval_packet_hash"]
    coverage_report_hash = retrieval_hashes["coverage_report_hash"]
    synthesis_context_packet_hash = sha256_json(synthesis_context_packet)
    llm_response = llm_backend.generate(synthesis_context_packet)

    next_thread_state, state_delta = _project_next_thread_state(
        thread_id=paths.thread_id,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        assistant_response=llm_response.assistant_response,
        turn_id=turn_id,
        timestamp=timestamp,
    )

    artifact_paths = _persist_turn_artifacts(
        turn_root=turn_root,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
    )

    user_message = {"role": "user", "content": user_input, "turn_id": turn_id, "timestamp": timestamp}
    assistant_message = {
        "role": "assistant",
        "content": llm_response.assistant_response,
        "turn_id": turn_id,
        "timestamp": timestamp,
    }
    thread_document["messages"] = list(thread_document.get("messages") or []) + [user_message, assistant_message]
    thread_document["turn_count"] = turn_id
    thread_document["updated_at"] = timestamp
    thread_document["latest_thread_state_hash"] = next_thread_state["latest_thread_state_hash"]

    ledger_record_base = {
        "thread_id": paths.thread_id,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "parent_perturbation_hash": parent_perturbation_hash,
        "prior_thread_state_hash": prior_thread_state_hash,
        "user_input_hash": sha256_text(user_input),
        "semantic_context_packet_hash": semantic_context_packet_hash,
        "semantic_traversal_manifest_hash": semantic_traversal_manifest_hash,
        "retrieval_packet_hash": retrieval_packet_hash,
        "coverage_report_hash": coverage_report_hash,
        "synthesis_context_packet_hash": synthesis_context_packet_hash,
        "assistant_response_hash": sha256_text(llm_response.assistant_response),
        "state_delta_hash": sha256_json(state_delta),
        "next_thread_state_hash": next_thread_state["latest_thread_state_hash"],
        "llm_call_metadata": llm_response.metadata,
    }
    state_perturbation_hash = sha256_json(ledger_record_base)
    ledger_record = dict(ledger_record_base)
    ledger_record["state_perturbation_hash"] = state_perturbation_hash

    append_ledger_record(paths.thread_ledger_path, ledger_record)

    thread_document["latest_perturbation_hash"] = state_perturbation_hash
    thread_document["ledger_record_count"] = turn_id

    write_json(paths.thread_state_path, next_thread_state)
    write_json(paths.conversation_thread_path, thread_document)

    return TurnExecutionResult(
        thread_id=paths.thread_id,
        turn_id=turn_id,
        thread_root=paths.thread_root,
        turn_root=turn_root,
        conversation_thread_path=paths.conversation_thread_path,
        thread_state_path=paths.thread_state_path,
        thread_ledger_path=paths.thread_ledger_path,
        semantic_context_packet_path=artifact_paths["semantic_context_packet_path"],
        semantic_traversal_manifest_path=artifact_paths["semantic_traversal_manifest_path"],
        retrieval_packet_path=artifact_paths["retrieval_packet_path"],
        coverage_report_path=artifact_paths["coverage_report_path"],
        synthesis_context_packet_path=artifact_paths["synthesis_context_packet_path"],
        state_delta_path=artifact_paths["state_delta_path"],
        assistant_response=llm_response.assistant_response,
        llm_metadata=llm_response.metadata,
        prior_thread_state=prior_thread_state,
        next_thread_state=next_thread_state,
        ledger_record=ledger_record,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
    )
