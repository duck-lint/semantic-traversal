# Security Policy

Semantic Traversal is an early-stage local-first project. It processes local files, invokes local and remote models, stores conversation artifacts, and accepts configuration that controls filesystem paths and external services.

## Reporting a vulnerability

Please report security-sensitive issues privately by email:

**duckklintt@gmail.com**

Include:

- a concise description of the issue;
- affected files, commands, or configuration;
- reproduction steps;
- the likely impact;
- any suggested mitigation;
- whether the report may be acknowledged publicly after resolution.

Please do not open a public GitHub issue for an unpatched vulnerability or include real API keys, private vault content, personal paths, or sensitive artifacts in a report.

No guaranteed response or remediation timeline is currently offered.

## Supported versions

The project does not yet publish versioned security releases. Security review applies to the current `stable-runtime` branch unless a report clearly concerns another active branch.

Older commits, forks, copied configuration, and local modifications are not supported as maintained security versions.

## Security-relevant boundaries

Users should understand the following:

- Vault content and generated artifacts may contain sensitive personal information.
- The configured frontier provider receives the bounded synthesis context for approved turns.
- Ollama and Sentence Transformers run locally under the current example configuration.
- API keys belong in `.env.local`, never in YAML, committed files, issue reports, or screenshots.
- The Obsidian plugin shells out to the configured Python runtime and therefore inherits the trust boundary of the selected executable, runtime root, configuration path, and vault.
- Filesystem paths in configuration should be treated as privileged input.
- Generated model text is not trusted executable output.
- The project has not undergone an independent security audit.

## Operational recommendations

- Use a dedicated virtual environment.
- Review `semantic_traversal.runtime.yaml` before execution.
- Keep `.env.local`, the vault, SQLite indexes, and thread artifacts out of version control.
- Inspect third-party dependency updates before installing them.
- Back up the vault before first-time ingestion or migration.
- Do not point the runtime at directories containing files you are unwilling to process.
- Treat exported diagnostics and retrieval packets as potentially sensitive.

## Scope

Useful reports include credential exposure, path traversal, unsafe file operations, command injection, unintended disclosure to model providers, authorization-boundary errors, dependency vulnerabilities with demonstrated impact, and corruption or overwrite risks.

General model hallucination, retrieval quality disagreement, prompt-injection theory without a demonstrated boundary failure, and unsupported deployment configurations should normally be filed as public issues rather than security reports.
