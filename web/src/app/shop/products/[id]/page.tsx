import { previewProductParams } from "@/lib/preview/static-params";
import ShopProductPage from "./shop-product-client";

// The page itself is a client component; this wrapper exists so the static preview can list its ids.
export function generateStaticParams() {
  return previewProductParams();
}

export default function Page() {
  return <ShopProductPage />;
}
