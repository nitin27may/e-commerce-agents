#!/usr/bin/env bash
# Picks the model provider for a Codespace from the variables that are present. Sourced by
# postCreate.sh and by scripts/test-codespaces-env.sh; it prints variable NAMES and modes, never a value.
#
# Order matters: Azure needs the endpoint AND a key, a base URL means an OpenAI-compatible server,
# a bare OPENAI_API_KEY means OpenAI, and nothing means replay (recorded responses, no key).

_present() { [ -n "${!1:-}" ] && [ "${!1}" != "sk-your-openai-api-key-here" ]; }

# codespaces_mode -> azure | openai-compatible | openai | replay
codespaces_mode() {
  if _present AZURE_OPENAI_ENDPOINT && _present AZURE_OPENAI_KEY; then
    echo azure
  elif _present LLM_BASE_URL; then
    echo openai-compatible
  elif _present OPENAI_API_KEY; then
    echo openai
  else
    echo replay
  fi
}

# codespaces_llm_provider <mode> -> the LLM_PROVIDER value the stack understands
codespaces_llm_provider() {
  case "$1" in
    azure) echo azure ;;
    replay) echo replay ;;
    *) echo openai ;;
  esac
}

# Marker on the first line of an .env this script wrote. Without it the file is the user's and is left alone.
CODESPACES_ENV_MARKER="# Written by .devcontainer/postCreate.sh. LLM_PROVIDER is re-derived from your Codespaces secrets on every rebuild; delete this line to take over the file."

# _drop_assignment <file> <VAR>: remove every VAR=... line
_drop_assignment() {
  local file="$1" var="$2" tmp
  tmp="$(mktemp)"
  awk -v var="$var" 'index($0, var "=") != 1 { print }' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# write_codespaces_env <repo-root>: create .env from .env.minimal, or re-derive the provider lines of one this
# script wrote. Secret values are never written: compose and pydantic read them from the environment.
write_codespaces_env() {
  local root="$1" mode provider env
  env="$root/.env"
  mode="$(codespaces_mode)"
  provider="$(codespaces_llm_provider "$mode")"

  if [ -f "$env" ]; then
    if [ "$(head -n 1 "$env")" != "$CODESPACES_ENV_MARKER" ]; then
      echo "==> .env exists and is yours; leaving it alone (the environment alone would select: ${mode})"
      return 0
    fi
  else
    { echo "$CODESPACES_ENV_MARKER"; sed -e 's/^OPENAI_API_KEY=/# OPENAI_API_KEY=/' "$root/.env.minimal"; } > "$env"
  fi

  _drop_assignment "$env" LLM_PROVIDER
  _drop_assignment "$env" EMBEDDING_PROVIDER
  {
    echo "LLM_PROVIDER=${provider}"
    # Replay vectors only make sense with replayed chat; a live provider embeds like its chat provider.
    if [ "$provider" = replay ]; then echo "EMBEDDING_PROVIDER=replay"; fi
  } >> "$env"
  echo "==> model provider: ${mode} (LLM_PROVIDER=${provider})"
}
