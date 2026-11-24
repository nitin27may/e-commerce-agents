import { previewOrderParams } from "@/lib/preview/static-params";
import OrderDetailPage from "./order-detail-client";

// The page itself is a client component; this wrapper exists so the static preview can list its ids.
export function generateStaticParams() {
  return previewOrderParams();
}

export default function Page() {
  return <OrderDetailPage />;
}
