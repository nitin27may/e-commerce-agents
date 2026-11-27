# shellcheck shell=bash
# Sourced by scripts/demo/tapes/evals.tape before the camera rolls: the keyless replay setup that
# scripts/replay-evals.sh uses, with the database and Redis of `docker compose up -d db redis`.
# Override DATABASE_URL / REDIS_URL in the calling shell if yours listen elsewhere.
export LLM_PROVIDER=replay
export REPLAY_FIXTURES_DIR=evals/fixtures/replay
export DATABASE_URL="${DATABASE_URL:-postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export ENVIRONMENT=test
export JWT_SECRET=ci-eval-jwt-secret-please-rotate-32bytes
export AGENT_SHARED_SECRET=ci-eval-agent-secret-please-rotate-32b
export PS1='$ '
