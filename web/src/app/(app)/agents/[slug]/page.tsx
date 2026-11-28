import { AGENTS } from "@/lib/agents";
import { AgentDetailClient } from "./agent-detail-client";
import { StaffOnly } from "@/components/staff-only";

export function generateStaticParams() {
  return AGENTS.map((a) => ({ slug: a.slug }));
}

// Next.js 16: params is a Promise — must be awaited
export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <StaffOnly>
      <AgentDetailClient slug={slug} />
    </StaffOnly>
  );
}
