# implementation-01 operator readme

## Purpose

`implementation-01` is the first-build-target local runtime for `semantic-traversal`. It proves the repo can accept a user turn, build a minimal `synthesis_context_packet`, persist `conversation_thread`, `thread_state`, and `thread_ledger` JSON artifacts, and return an assistant response through either the stub or live LLM path.

This runtime does not add traversal, retrieval, graph work, synthetic-node promotion, or database/UI surfaces.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Run stub mode

CLI runner:

```powershell
python -m semantic_traversal --message "Hello from stub mode." --llm-mode stub
```

Named probes:

```powershell
python -m semantic_traversal.probes probe_new_thread_minimal_turn --llm-mode stub
python -m semantic_traversal.probes probe_same_thread_continuation_turn --llm-mode stub
```

Stub mode proves the local CLI/runtime path, JSON persistence, thread continuity, and hash-chained ledger behavior without requiring the OpenAI SDK or network access.

## Run live mode

`OPENAI_API_KEY` is required for live mode. Set it in the current PowerShell session or place it in a repo-root `.env.local`.

```powershell
$env:OPENAI_API_KEY = "sk-..."
python -m semantic_traversal --message "Hello from live mode." --llm-mode live
```

Optional model override:

```powershell
python -m semantic_traversal --message "Hello from live mode." --llm-mode live --model gpt-4.1-mini
```

If you prefer `.env.local`, use:

```text
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
```

Do not commit `.env.local`.

Live mode proves the runtime can resolve OpenAI settings, lazily import the OpenAI SDK only on the live path, and complete a real provider call through the same local runner.

## Runtime artifacts

The CLI default artifact root is `$env:TEMP\semantic-traversal`. The named probes default to `$env:TEMP\semantic-traversal-probes`. Both roots create:

- `threads/<thread_id>/conversation_thread.json`
- `threads/<thread_id>/thread_state.json`
- `threads/<thread_id>/thread_ledger.jsonl`

You can override either root with `--data-root`.

## Inspect thread artifacts

List generated thread folders:

```powershell
Get-ChildItem "$env:TEMP\semantic-traversal\threads" -Recurse
```

Inspect the persisted transcript and continuity state:

```powershell
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\conversation_thread.json"
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\thread_state.json"
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\thread_ledger.jsonl"
```

`conversation_thread.json` stores the visible transcript plus hash pointers, `thread_state.json` stores the current materialized continuity snapshot, and `thread_ledger.jsonl` stores the append-only hash-chained turn records.
