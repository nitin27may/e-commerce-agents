import { PREVIEW } from "./config";
import raw from "./preview-data.json";

/**
 * `generateStaticParams` for the dynamic routes the static preview exports (#301). A static export builds one
 * page per id, so the preview lists the snapshot's; a normal build lists none and renders on demand as before.
 */
export function previewProductParams(): { id: string }[] {
  return PREVIEW ? raw.products.map((p) => ({ id: p.id })) : [];
}

export function previewOrderParams(): { id: string }[] {
  return PREVIEW ? raw.orders.map((o) => ({ id: o.id })) : [];
}
