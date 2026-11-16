import { ErrorState } from "@/components/error-state";

export default function NotFound() {
  return <ErrorState kind="not-found" fullScreen />;
}
