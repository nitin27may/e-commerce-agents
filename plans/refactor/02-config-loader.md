# Refactor 02 — Central Config Loader

## Goal

Centralize all LLM / session / checkpoint / embeddings factory logic in `agents/shared/config.py`. Downstream modules never read env vars directly.

## Deliverables

- `shared/config.py` — Pydantic `Settings` class exposing typed fields for every env var.
- Factory functions:
  - `get_chat_client() -> ChatClientBase`
  - `get_embeddings_client() -> EmbeddingsClient`
  - `get_session_storage() -> SessionStorage`
  - `get_checkpoint_storage() -> CheckpointStorage`
  - `get_agent_registry() -> dict[str, str]`
- Update every specialist and the orchestrator to use these factories (no more scattered `os.getenv` reads).

## Test file

`agents/tests/test_config_loader.py` — minimum 6 tests:

- `get_chat_client()` returns `OpenAIChatClient` when `LLM_PROVIDER=openai`.
- Returns `AzureOpenAIChatClient` when `=azure`, with endpoint/key/deployment propagated.
- `get_session_storage()` honors `MAF_SESSION_BACKEND=postgres|file|memory`.
- `get_checkpoint_storage()` honors `MAF_CHECKPOINT_BACKEND` and uses `MAF_CHECKPOINT_DIR` for file mode.
- `get_agent_registry()` parses JSON from `AGENT_REGISTRY` and returns a dict.
- Malformed `AGENT_REGISTRY` raises `ConfigError` with a helpful message.

## Verification

- No `os.getenv` calls survive outside `shared/config.py` (grep check in CI).
- Existing `pytest agents/tests/` green.
- All specialists boot and respond identically pre- and post-refactor.

## Out of scope

- Any feature change; purely refactor.
