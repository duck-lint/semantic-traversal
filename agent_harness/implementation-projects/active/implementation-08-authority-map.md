# Implementation 08 — Seam 0 Authority Map

## Status

Seam 0: operator-accepted

| Concern | Authoritative surface | Seam-0 treatment |
| --- | --- | --- |
| Raw user utterance | Current runtime input packet | Preserve exactly; evaluation records it indirectly through scenario input, not private data |
| Retrieval policy and limits | Runtime/YAML | Frozen |
| Scope, fusion, selection, coverage, claim permission | Runtime/YAML | Frozen |
| Lexical FTS5 grammar serialization | `semantic_traversal/runtime.py` | Only production data-plane repair permitted |
| Current Seam-0 route treatment | Evaluation schema only | `direct` or `traverse`; production route field is not fabricated |
| Future production route request | Control assistant | Requests `direct` or `traverse`; not implemented in Seam 0 |
| Future executable route authority | Runtime | Validates and executes the accepted route; not implemented in Seam 0 |
| Outcome | Evaluation schema | Separate values such as `answered`, `blocked_control`, `blocked_retrieval`, `blocked_zero_evidence`, `failed_synthesis`, `unsupported` |
| User-visible answer | Frontier synthesizer | Not written by the control assistant; production routing is frozen |
| Future focus-state transitions | Runtime | `conversation_focus` and `retrieval_focus` remain distinct; blocked turns preserve both and fallback/unapproved candidates update neither |
| Conversation context | Existing runtime state | Evaluation may record carried referents; no focus migration |
| Private corpus facts | Operator-supplied UAT | Never committed to generic fixtures or baseline records |

The later context design must preserve distinct concepts equivalent to `conversation_focus` and `retrieval_focus`; Seam 0 does not implement or rename them.
