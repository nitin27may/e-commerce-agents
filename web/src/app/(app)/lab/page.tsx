import { ChatView } from "@/components/chat/chat-view";
import { StaffOnly } from "@/components/staff-only";

/**
 * The Lab: the same chat with its machinery shown, for staff and for developers following the course. Pick an
 * orchestration mode, compare modes on one prompt, and see which agents answered, every step and tool call, the
 * grounding report and the live workflow graph. Never linked from a shopper's pages.
 */
export default function LabPage() {
  return (
    <StaffOnly>
      <ChatView devView />
    </StaffOnly>
  );
}
