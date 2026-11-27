/**
 * High-quality capture for the documentation media (scripts/record-media.sh).
 *
 * Why not Playwright's `recordVideo`: it encodes VP8 at a fixed low bitrate and at device scale 1, so small chat
 * text is soft before any GIF encoder sees it. This grabs lossless PNG frames at the page's device scale (2 by
 * default), each with its capture time, and scripts/demo/encode.sh rebuilds a constant-rate timeline from them.
 *
 * Also here: a visible pointer with a click ripple (headless Chromium draws no cursor) and a gliding click, so a
 * viewer can follow what is being pressed.
 */

import type { CDPSession, Locator, Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

export interface Recording {
  stop(): Promise<{ dir: string; frames: number; seconds: number; fps: number }>;
}

/**
 * Start writing PNG frames of `page` to `dir` (created, and emptied of earlier frames).
 *
 * Frames are grabbed with Page.captureScreenshot at the page's device scale, in a loop, each stamped with the time
 * it was taken. Page.startScreencast would be cheaper, but it delivers frames at device scale 1 whatever the page's
 * scale is, which throws away the point of recording at 2. At 1280 x 800 x 2 the loop runs at about 13 frames a
 * second on an M-series Mac, which is plenty for a UI clip encoded at 12 to 15.
 */
export async function startScreencast(page: Page, dir: string): Promise<Recording> {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const client: CDPSession = await page.context().newCDPSession(page);
  const viewport = page.viewportSize() ?? { width: 1280, height: 800 };
  const scale = await page.evaluate(() => window.devicePixelRatio);
  const clip = { x: 0, y: 0, width: viewport.width, height: viewport.height, scale };
  const timeline: { file: string; t: number }[] = [];
  const writes: Promise<void>[] = [];
  let running = true;

  const loop = (async () => {
    let n = 0;
    while (running) {
      const t = Date.now() / 1000;
      const shot = await client
        .send("Page.captureScreenshot", { format: "png", optimizeForSpeed: true, clip })
        .catch(() => null);
      if (!shot) continue;
      const file = `f${String(n++).padStart(6, "0")}.png`;
      timeline.push({ file, t });
      writes.push(fs.promises.writeFile(path.join(dir, file), Buffer.from(shot.data, "base64")));
    }
  })();

  return {
    async stop() {
      // Hold the last frame on screen so the GIF does not loop the instant the final beat lands.
      await page.waitForTimeout(300);
      running = false;
      await loop;
      await Promise.all(writes);
      await client.detach().catch(() => {});
      if (timeline.length === 0) throw new Error("the capture produced no frames");
      // ffmpeg concat list: each frame lasts until the next one; the last one is held for 1.5 s.
      const lines: string[] = [];
      for (let i = 0; i < timeline.length; i++) {
        const next = timeline[i + 1]?.t ?? timeline[i].t + 1.5;
        lines.push(`file '${timeline[i].file}'`, `duration ${Math.max(0.001, next - timeline[i].t).toFixed(4)}`);
      }
      lines.push(`file '${timeline[timeline.length - 1].file}'`);
      fs.writeFileSync(path.join(dir, "frames.txt"), lines.join("\n") + "\n");
      const seconds = timeline[timeline.length - 1].t - timeline[0].t + 1.5;
      return { dir, frames: timeline.length, seconds, fps: Number((timeline.length / seconds).toFixed(1)) };
    },
  };
}

/**
 * A visible pointer and a ripple on every click. Installed before any page script runs, so it survives navigation.
 * Pure CSS on a fixed, pointer-events:none layer: it cannot intercept a click or shift layout.
 */
export function showPointer(page: Page) {
  return page.addInitScript(() => {
    const install = () => {
      if (document.getElementById("__media-pointer")) return;
      const style = document.createElement("style");
      style.textContent = `
        #__media-pointer { position: fixed; left: 0; top: 0; width: 22px; height: 22px; margin: -3px 0 0 -3px;
          pointer-events: none; z-index: 2147483647; transition: transform 40ms linear;
          filter: drop-shadow(0 1px 2px rgba(0,0,0,.35)); }
        .__media-ripple { position: fixed; width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%;
          pointer-events: none; z-index: 2147483646; border: 2px solid rgba(37,99,235,.85);
          background: rgba(37,99,235,.18); animation: __media-ripple 520ms ease-out forwards; }
        @keyframes __media-ripple { from { transform: scale(.35); opacity: 1 } to { transform: scale(1.25); opacity: 0 } }`;
      document.head.appendChild(style);
      const pointer = document.createElement("div");
      pointer.id = "__media-pointer";
      pointer.innerHTML =
        '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M4 2l15 11.2-6.6.9 3.9 7.4-2.9 1.5-3.9-7.5L4 20z" ' +
        'fill="#111827" stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      pointer.style.transform = "translate(-100px,-100px)";
      document.body.appendChild(pointer);
      window.addEventListener("mousemove", (e) => {
        pointer.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      }, true);
      window.addEventListener("mousedown", (e) => {
        const ripple = document.createElement("div");
        ripple.className = "__media-ripple";
        ripple.style.left = `${e.clientX}px`;
        ripple.style.top = `${e.clientY}px`;
        document.body.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);
      }, true);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
    else install();
  });
}

let last = { x: 640, y: 400 };

/** Move the pointer to the middle of `target` in visible steps, pause, then click: what a person's hand does. */
export async function glideClick(page: Page, target: Locator, opts: { settleMs?: number } = {}) {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (!box) throw new Error(`glideClick: ${target} has no box`);
  const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  const distance = Math.hypot(to.x - last.x, to.y - last.y);
  await page.mouse.move(to.x, to.y, { steps: Math.max(8, Math.min(40, Math.round(distance / 18))) });
  last = to;
  await page.waitForTimeout(opts.settleMs ?? 180);
  await page.mouse.down();
  await page.mouse.up();
}

/** Park the pointer somewhere neutral so it does not sit on top of the content being read. */
export async function parkPointer(page: Page, at = { x: 1180, y: 760 }) {
  await page.mouse.move(at.x, at.y, { steps: 20 });
  last = at;
}
