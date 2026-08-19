// The Chat section: conversation list (sidebar on md+, full screen on
// mobile) plus the conversation surface. Routes:
//   /chats          → list (mobile) / list + new chat (md+)
//   /chats/new      → a fresh chat surface
//   /chats/:id      → an existing conversation, continued in place
// One route entry with an optional param keeps this component mounted across
// navigation, so an in-flight stream survives the create→navigate hop.

import { useParams } from "react-router-dom";
import { ChatView } from "../components/chat/ChatView";
import { ConversationList } from "../components/chat/ConversationList";
import { cx } from "../lib/utils";

export default function Chats() {
  const { conversationId: param } = useParams();
  const conversationId = param && param !== "new" ? param : null;
  const chatOpen = param != null;

  return (
    <div className="md:grid md:grid-cols-[280px_minmax(0,1fr)]">
      {/* Conversation list — full screen on mobile, sticky pane on md+ */}
      <div
        className={cx(
          "md:block md:border-r md:border-border",
          chatOpen ? "hidden" : "block",
        )}
      >
        <div className="h-dvh md:sticky md:top-0">
          <ConversationList activeId={conversationId} />
        </div>
      </div>

      {/* Chat surface */}
      <div className={cx("min-w-0", chatOpen ? "block" : "hidden md:block")}>
        <ChatView conversationId={conversationId} />
      </div>
    </div>
  );
}
