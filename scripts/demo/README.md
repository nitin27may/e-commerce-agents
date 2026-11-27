# Demo media: recording and re-recording

Every GIF and video in the docs is produced by a script, so a UI change is followed by a re-record, not by a stale clip.

```bash
./scripts/dev.sh --no-key            # the stack, with recorded model answers: no key, no cost
scripts/record-media.sh              # every app clip  -> docs/images/<name>.gif + docs/media/<name>.mp4 (not committed)
scripts/record-media.sh hero         # one clip, by the scenario name in web/e2e/demo-media.spec.ts
scripts/record-media.sh evals        # a terminal clip, from scripts/demo/tapes/evals.tape
```

Needs `ffmpeg` and `gifski`, plus `vhs` for terminal clips (`brew install ffmpeg gifski vhs`).

| File | What it does |
|---|---|
| `web/e2e/demo-media.spec.ts` | One Playwright test per clip. Skipped unless `RECORD_MEDIA=1` |
| `web/e2e/media/screencast.ts` | Frame capture, the visible pointer and click ripple, gliding clicks |
| `scripts/demo/encode.sh` | Frames to GIF (gifski) and MP4 (H.264) |
| `scripts/demo/tapes/*.tape` | Terminal clips (vhs) |
| `scripts/demo/eval-env.sh` | The keyless eval environment the evals tape sources off camera |

## Settings, and why

| Setting | Value | Why |
|---|---|---|
| Viewport | 1280 x 800, light theme | One frame for every clip, so the set reads as one product. 16:10 matches a laptop screen |
| Capture | PNG frames at device scale 2 (2560 x 1600), each stamped with its capture time | Playwright's `recordVideo` encodes low-bitrate VP8 at scale 1, so small text is soft before any encoder sees it. The DevTools screencast ignores the device scale, so frames come from `Page.captureScreenshot`, about 13 a second |
| GIF | 1280 px wide, 13 fps, gifski `--quality 95 --motion-quality 100 --lossy-quality 100` | GitHub renders GIFs inline in a README and does not play video files committed to the repo. A downscale from 2x keeps text crisp. Lower motion or lossy quality leaves ghosting in flat backgrounds. Budget: 8 MB per clip |
| MP4 | 2560 px wide, H.264 high profile, CRF 18, 30 fps, `+faststart` | For articles, the docs site and release notes. Not committed (`docs/media/` is ignored): every re-record would add megabytes to the history. Attach it to a release, or upload it to a GitHub issue or PR for a `user-attachments` URL that a README can play inline |
| Pointer | Drawn arrow with a click ripple; moves glide | Headless Chromium draws no cursor, and without one a viewer cannot tell what was clicked |
| Pacing | Typing at about 45 characters a second; SSE frames spaced 450 to 700 ms; a pause after each answer long enough to read it once | Workflow modes finish in 100 to 300 ms on the replay stack, which would flash past |
| Terminal | vhs, 1280 x 800, Menlo 15, GitHub Dark | Readable at README width |

## Gotchas

- **Unrecorded prompts.** The no-key stack answers only recorded prompts (`agents/python/demo/fixtures/manifest.json`). A new prompt is a replay miss until it is recorded ([replay mode](../../docs/replay-mode.md)), and the spec fails the take rather than record a miss.
- **Stale date buckets.** Tools that bucket by date (the review trend) drift from their recordings as the calendar moves. Re-record those prompts.
- **Busy ports.** If ports 5432, 6379 or 8080 are taken on your machine, move the stack with a local compose override (`ports: !override [...]`) and point `E2E_API_URL` at the orchestrator's new port.
- **Account details.** Never record a terminal or portal showing a real subscription, tenant, account name or IP address. Check a clip frame by frame before committing it.
