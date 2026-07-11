# Semantic traversal Obsidian plugin

Desktop-only V1 interface for the external Python runtime.

The plugin does not own or rewrite `semantic_traversal.runtime.yaml`. Configure
these operational pointers in the plugin settings:

- Python executable, defaulting to `python`.
- Runtime root containing the Python package.
- External runtime YAML path.
- Artifact root containing `threads/`.

The YAML should resolve its data root to the vault's hidden artifact directory,
for example `<vault>/.semantic_traversal`. The plugin renders persisted
`conversation_thread.json` files from that location. It does not automatically
ingest the vault.

The view displays persisted `assistant` messages as **Agent** responses, rendered
with Obsidian Markdown. Its short-lived **Assistant** status only denotes local
context preparation before the Agent produces the user-facing response. Raw
thread IDs remain available as select-control tooltips and in diagnostics; the
visible thread label is derived from the first user message.

Build with `npm run build`. The generated `main.js`, together with
`manifest.json` and `styles.css`, can then be copied into the vault's
`.obsidian/plugins/semantic-traversal/` directory for local testing.
