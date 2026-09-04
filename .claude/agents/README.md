# Claude Code model strategy & subagents

This repo configures Claude Code in two layers. The split between "plan on Opus,
build on Sonnet" is a **session-model** concern, not a subagent concern — so it lives
in `settings.json`, and the subagents below cover the *other* activities where a
different model genuinely helps.

## Layer 1 — session model (`.claude/settings.json`)

```json
{ "model": "opusplan" }
```

`opusplan` runs **Opus while in Plan mode** and **Sonnet for normal interaction and
implementation**. That is the entire "planning → Opus, coding → Sonnet" requirement,
done at the level that actually controls it.

Why not do this with a subagent? A subagent's `model:` field only governs work that
Claude *delegates to that subagent*. It does **not** govern Plan mode or the main
implementation loop — those run on the session model. So no `.claude/agents/*.md`
setting can make "planning use Opus while coding uses Sonnet"; `opusplan` is the
correct mechanism.

### Caveats (read these)

- **Opus access required.** `opusplan` needs Opus entitlement (Claude Max, or
  API/Enterprise). On the Pro plan Opus is unavailable and this setting won't engage.
- **This is a checked-in default, not a lock.** Project `settings.json` overrides a
  collaborator's `~/.claude/settings.json` model, but a per-session `/model` (or
  `--model` / `ANTHROPIC_MODEL`) still wins. Anyone who can't use Opus can override
  with `/model sonnet`. To make it personal instead of shared, move the `model` key
  into `.claude/settings.local.json` (gitignored).
- **Env vars silently win.** Session model precedence is roughly
  `/model` > `--model` > `ANTHROPIC_MODEL` env > settings file. Subagent model
  precedence is `CLAUDE_CODE_SUBAGENT_MODEL` env > per-invocation > frontmatter >
  main model. If you export `ANTHROPIC_MODEL` or `CLAUDE_CODE_SUBAGENT_MODEL`
  anywhere in your shell profile, it overrides everything here.
- **No 1M context in the plan phase.** The automatic 1M-context upgrade applies to
  the plain `opus` setting, not `opusplan` — the plan-mode Opus phase uses the
  standard context window. For planning against very large contexts, prefer a manual
  `/model opus` session.
- **Aliases vs pinned IDs.** The agents below use aliases (`opus`, `sonnet`,
  `haiku`), which is the default here on purpose: an alias follows the current
  generation without an edit, and a pinned ID silently keeps you on an old model
  long after it stops being the right one. To pin anyway, swap the alias for a
  full ID (`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`) and
  put a review date on it. `opusplan` itself cannot be pinned — pinning means
  managing the plan/code split manually.

## Layer 2 — subagents (`.claude/agents/*.md`)

Activity-focused, project-tuned, and deliberately small to avoid auto-delegation
noise. Each is invoked by Claude based on its `description` (or explicitly by name).

| Agent | Model | Use it for |
|-------|-------|-----------|
| `explorer` | haiku | Fast read-only codebase search / file discovery. Cheap, high-volume; keeps verbose output out of the main context. |
| `test-runner` | sonnet | Runs pytest / .NET / Playwright, reports only failures. Isolates noisy test output. |
| `planner` | opus | Phased PR-sized implementation plans, design-thinking, architecture/pattern decisions — before code. |
| `architecture-reviewer` | opus | System-design & security-boundary review (agent/tool boundaries, guardrails, A2A, MAF idiom). High-level. |
| `code-auditor` | opus | Independent line-level review of recent changes before merge (correctness, security, tests, conventions). |

### Why no "coder" subagent

Coding happens in the **main loop**, which is Sonnet under `opusplan` — that already
*is* the coding model. A standalone "coder" subagent would just be a second Sonnet
with less context and no benefit. Implementation stays in the main session.

### Avoiding sprawl

Resist one-agent-per-language/feature growth. `description`-based auto-delegation
gets noisy and unpredictable as the fleet grows, and you lose track of which agent
fired. Five focused agents covering explore / test / plan / design-review /
code-review is the intended ceiling for this repo.
