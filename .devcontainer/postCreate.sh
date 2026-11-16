#!/usr/bin/env bash
# Runs once when the dev container is created. No secrets are needed or read.
set -euo pipefail

cd "$(dirname "$0")/.."

# pnpm, pinned to the version CI uses (the web jobs in the tests workflow).
corepack enable
corepack prepare pnpm@10.15.0 --activate

# uv is not in the base image.
if ! command -v uv >/dev/null 2>&1; then
  pip install --user --quiet uv
  export PATH="$HOME/.local/bin:$PATH"
fi

# .env for scripts/dev.sh: the minimal template, switched to replay so it starts
# with no key. An existing .env is never overwritten.
if [ ! -f .env ]; then
  sed -e 's/^LLM_PROVIDER=.*/LLM_PROVIDER=replay/' \
      -e 's/^OPENAI_API_KEY=/# OPENAI_API_KEY=/' .env.minimal > .env
fi

echo "==> tutorials (Python)"
uv sync --project tutorials --extra dev

echo "==> agents (Python, all workspace packages)"
(cd agents/python && uv sync --all-packages --extra dev)

echo "==> web"
(cd web && pnpm install --frozen-lockfile)

echo "==> versions"
python --version
uv --version
node --version
pnpm --version
dotnet --version
