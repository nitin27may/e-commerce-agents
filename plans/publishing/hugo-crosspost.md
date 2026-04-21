# Publishing Playbook — Hugo Cross-Post

## Goal

Define the step-by-step workflow for publishing a tutorial chapter article to nitinksingh.com (PaperMod-style Hugo theme) from the canonical source in `tutorials/<chapter>/README.md`.

## Per-chapter publishing workflow

1. **Code lands first**. The chapter's PR merges with `python/`, `dotnet/`, `tests/`, `PLAN.md`, and `README.md` (front matter `draft: true`).
2. **Record video demo**. Author records screen capture of both `python/` and `dotnet/` examples running end-to-end + any Aspire screenshots.
3. **Cross-post to Hugo blog**:
   - Copy `tutorials/<chapter>/README.md` → `<hugo-blog-repo>/content/posts/maf-v1-<slug>.md`.
   - Update image paths: `cover.image: img/posts/maf-v1-<slug>.jpg` (uploaded separately to `static/img/posts/`).
   - Add video embed (YouTube / Vimeo iframe) after the concept section.
   - Set `draft: false`.
   - Set `date` and `lastmod` to publish date.
   - `hugo build && hugo deploy` (or the equivalent for the blog's deployment).
4. **Inject superseded-by banner on the old article** (only if the new chapter supersedes one — see master plan *Reuse Matrix*):
   - Edit the old article in the Hugo repo. Prepend the body with:
     ```markdown
     > **Updated version available.** A revised version with .NET examples is available: [MAF v1 — &lt;title&gt;](/posts/maf-v1-&lt;slug&gt;/). This original article remains for historical reference.
     ```
   - Commit and redeploy.
5. **Checkbox the chapter** in `tutorials/README.md` (flip status from "Draft" to "Published" with the article URL).
6. **Announce** on the usual channels (LinkedIn, Reddit, Twitter).

## Front matter template (Hugo PaperMod)

```yaml
---
title: "MAF v1 — <Chapter Title>"
date: 2026-04-XX
lastmod: 2026-04-XX
draft: false
tags: [microsoft-agent-framework, ai-agents, python, dotnet, <concept-tag>]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "<one-line description>"
cover:
  image: "img/posts/maf-v1-<slug>.jpg"
  alt: "<alt text>"
author: "Nitin Kumar Singh"
toc: true
---
```

## Cover image spec

- Dimensions: 1200 × 630 px (OpenGraph / Twitter card standard).
- File: `img/posts/maf-v1-<slug>.jpg`, JPEG quality 85.
- Naming: matches chapter slug so image and article stay aligned.

## Slug naming convention

`maf-v1-<chapter-slug>` where `<chapter-slug>` matches the `tutorials/` folder suffix (e.g., `first-agent`, `add-tools`, `sequential-orchestration`). Full: `/posts/maf-v1-first-agent/`.

## Superseded-by banner — when to inject

Refer to the master plan's *Existing Article Series — Reuse Matrix*. Only inject the banner on articles marked as SUPERSEDED or REUSE-WITH-UPDATE. REUSE-AS-IS articles don't need a banner.

## Metrics to track per published article

- Page views (Google Analytics)
- Reading-time completion rate
- GitHub stars / tutorial folder views
- Comments / issues filed against `tutorials/<chapter>/`

## Not automated in v1

- Automatic slug generation from file naming.
- Automatic banner injection on old articles.
- Video transcoding pipeline.

These are tracked as follow-ups if the chapter cadence warrants them.
