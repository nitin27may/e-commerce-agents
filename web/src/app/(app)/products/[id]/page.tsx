import { previewProductParams } from "@/lib/preview/static-params";
import ProductDetailPage from "./product-detail-client";

// The page itself is a client component; this wrapper exists so the static preview can list its ids.
export function generateStaticParams() {
  return previewProductParams();
}

export default function Page() {
  return <ProductDetailPage />;
}
