import { ChatView } from "@/components/chat-view";
import { PageHeader } from "@/components/page-header";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Ask" title="Consult" description="Put a question to your library. Every answer cites the documents it came from." />
      <ChatView />
    </>
  );
}
