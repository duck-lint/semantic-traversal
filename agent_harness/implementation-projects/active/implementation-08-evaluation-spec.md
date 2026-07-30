# Implementation 08 — Seam 0 Generic Evaluation Specification

## Status

Seam 0: in progress

## Record schema

Each terminal case record contains: `scenario_id`, `turn_id`, `thread_id`, `input`, `expected_response_mode`, `emitted_response_mode`, `baseline_capability_gap`, `expected_current_turn_subjects`, `emitted_current_turn_subjects`, `resolved_referents`, `carry_source`, `requested_operators`, `executed_operators`, `evidence_requirements`, `requiredness`, `plan_completeness`, `plan_executability`, `execution_outcome`, `blocking_reason`, `candidate_count_by_surface`, `answer_bearing_candidate_present`, `selected_evidence_present`, `irrelevant_selected_evidence_count`, `malformed_status`, `planner_latency_ms`, `retrieval_latency_ms`, `synthesis_latency_ms`, `available_planner_input_tokens`, `available_planner_output_tokens`, `available_planner_cost`, `available_synthesis_tokens`, `available_synthesis_cost`, `final_support_grade`, `terminal_status`, and raw/canonical outputs when available.

`expected_response_mode` and `execution_outcome` are independent. `blocked` is never a response mode.

## Generic scenarios

| ID | Shape | Turns | Expected mode / outcome focus |
| --- | --- | --- | --- |
| direct-casual | Casual greeting | one | direct; answered |
| direct-explanation | General-knowledge explanation | one | direct; answered |
| direct-rewrite | Rewrite supplied generic text | one | direct; answered |
| user-traversal | User-specific traversal request | one | traverse; answered or blocked retrieval |
| narrow-fact | Narrow corpus fact | one | traverse; evidence-bearing |
| exact-phrase | Exact phrase | one | traverse; exact evidence |
| exact-count | Exact count | one | traverse; exhaustive evidence |
| exact-absence | Exact absence/negative claim | one | traverse; coverage-gated outcome |
| lexical-punctuation | Apostrophes and punctuation | one | traverse; lexical candidate/zero/skip/failure distinctions |
| broad-synthesis | Broad conceptual synthesis | one | traverse; synthesis support |
| temporal-origin | Origin/genealogy | one | traverse; chronology required |
| temporal-order | Ordering | one | traverse; chronology required |
| graph-relation | Graph relationship | one | traverse; graph evidence |
| cross-note | Cross-note comparison | one | traverse; comparative evidence |
| referential-continuation | Setup names two generic concepts; continuation asks which is earlier | two, one persisted thread | carry both referents from prior turn; traverse |
| comparative-continuation | Setup establishes two generic alternatives; continuation asks which is broader | two, one persisted thread | carry comparison set; traverse |
| topic-reset | Setup establishes a topic; continuation asks unrelated generic question | two, one persisted thread | reset carried referents; direct or new target per current schema |
| ambiguous-reference | Setup establishes multiple plausible referents; continuation uses ambiguous pronoun | two, one persisted thread | preserve ambiguity; blocked/clarification outcome, not invented referent |
| unsupported-private | Requests unavailable private fact | one | traverse; blocked_zero_evidence or unsupported |
| planner-unavailable | Controlled unavailable backend | one | traverse; blocked_control |
| planner-timeout | Controlled timeout backend | one | traverse; blocked_control |
| planner-malformed | Controlled malformed backend | one | traverse; blocked_control or malformed |
| zero-evidence | Valid plan, empty retrieval | one | traverse; blocked_zero_evidence |
| restart-recovery | Setup turn persisted, planner backend restarted, continuation supplied | two, one persisted thread | carried referents survive restart; terminal outcome persisted |

Referential, comparative, reset, and restart scenarios are real multi-turn cases sharing one persisted thread per scenario. They are not scored from isolated continuation sentences.

## Scoring

Score separately: route correctness, current-turn target correctness, referent correctness, plan correctness, candidate recall, selection precision, and synthesis support. A successful-looking final answer cannot establish route correctness.

## Failure injection

Unavailable, timeout, and malformed cases use deterministic backend doubles. Magic strings sent to a live model are not valid failure injection.
