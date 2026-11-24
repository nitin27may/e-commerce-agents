#!/usr/bin/env bash
# Runs when the dev container is created or rebuilt. It checks which model variables are
# present and never prints or writes their values.
set -euo pipefail

cd "$(dirname "$0")/.."

# pnpm, pinned to the version CI uses (the web jobs in the tests workflow).
# Corepack is not bundled with Node 25+, so install pnpm from npm.
npm install -g pnpm@10.15.0

# uv is not in the base image.
if ! command -v uv >/dev/null 2>&1; then
  pip install --user --quiet uv
  export PATH="$HOME/.local/bin:$PATH"
fi

# .env for scripts/dev.sh. The provider comes from whichever Codespaces secrets are present
# (provider.sh); no secret value is written to the file. An .env you took over is never touched.
# shellcheck source=/dev/null
source .devcontainer/provider.sh
write_codespaces_env "$PWD"

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
