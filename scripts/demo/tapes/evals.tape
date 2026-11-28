# Keyless evals in the terminal: a specialist scored against its baseline, then the red-team suite.
# Run from the repository root (scripts/record-media.sh evals does it):  vhs scripts/demo/tapes/evals.tape
# Needs Postgres and Redis seeded (docker compose up -d db redis, then the seeder) and uv.

Output docs/media/evals.mp4

Set Shell "bash"
Set FontSize 15
Set FontFamily "Menlo"
Set Width 1280
Set Height 800
Set Padding 28
Set Theme "GitHub Dark"
Set TypingSpeed 35ms
Set Framerate 30
Set CursorBlink false

Hide
Type "cd agents/python && source ../../scripts/demo/eval-env.sh && clear" Enter
Sleep 500ms
Show

Type "# Score a specialist against its committed baseline. No API key: recorded answers." Enter
Sleep 600ms
Type "uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --baseline-from reference" Enter
Wait+Screen@60s /Evaluation (PASSED|FAILED)/
Sleep 4.5s

Hide
Type "clear" Enter
Show
Type "# The red-team suite: prompt injection and jailbreak attempts." Enter
Sleep 600ms
Type "uv run python -m evals.run_evals --suite safety" Enter
Wait+Screen@60s /Safety suite (PASSED|FAILED)/
Sleep 4.5s
