import { PREVIEW, QUICKSTART_URL } from "@/lib/preview/config";

/**
 * The static preview's banner (#301). Renders nothing in a normal build. The page below it gives up the
 * banner's height through the `html[data-preview]` rules in globals.css.
 */
export function PreviewBanner() {
  if (!PREVIEW) return null;
  return (
    <div
      role="note"
      data-testid="preview-banner"
      className="sticky top-0 z-[60] flex h-9 items-center justify-center gap-2 bg-amber-100 px-3 text-xs text-amber-950 dark:bg-amber-950 dark:text-amber-100"
    >
      <span className="font-semibold">Preview: recorded answers.</span>
      <span className="hidden sm:inline">No model or server is behind this page.</span>
      <a href={QUICKSTART_URL} className="font-medium underline underline-offset-2" target="_blank" rel="noreferrer">
        Run it locally
      </a>
    </div>
  );
}
