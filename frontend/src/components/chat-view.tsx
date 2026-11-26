"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, BookOpenText, Loader2, MessageSquarePlus, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AnswerText } from "@/components/answer-text";
import { usePoll } from "@/hooks/use-poll";
import { Button } from "@/components/ui/button";
import { chatApi, streamAnswer, type ChatMessage, type ChatSource, type ConversationSummary } from "@/lib/chat";
import { sourceLabel, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "What are the main topics across my documents?",
  "Summarise the most recent documents I added.",
  "Which people or organisations are mentioned most?",
];

function SourceCards({ messageId, sources, highlighted }: { messageId: string | number; sources: ChatSource[]; highlighted: number | null }) {
  // Only what the answer actually cites: when it says "not found", unrelated passages would just be noise.
  const shown = sources.filter((s) => s.cited);
  if (!shown.length) return null;
  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        Sources
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {shown.map((s) => (
          <li
            key={s.n}
            id={`src-${messageId}-${s.n}`}
            className={cn(
              "rounded-md border bg-background/60 p-3 transition-colors",
              highlighted === s.n ? "border-brass bg-brass/10" : "border-border",
            )}
          >
            <div className="flex items-start gap-2">
              <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-brass/60 bg-brass/10 px-1 text-[11px] font-semibold text-brass">
                {s.n}
              </span>
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium">{s.title}</p>
                {s.source_id && <p className="text-[11px] text-muted-foreground">{sourceLabel(s.source_id)}</p>}
              </div>
            </div>
            <p className="mt-2 line-clamp-3 text-[12px] leading-relaxed text-muted-foreground">{s.snippet}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function IndexingBanner() {
  const { data } = usePoll(chatApi.status, { intervalMs: 10_000, fastMs: 5_000, fast: true });
  if (!data || data.pending === 0) return null;
  const busiest = data.sources.filter((s) => s.pending > 0).slice(0, 3);
  return (
    <div className="flex items-start gap-3 border-b border-border bg-brass/10 px-5 py-2.5 text-[13px]">
      <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-brass" />
      <p className="leading-relaxed">
        <span className="font-medium">
          Still reading your documents: {data.indexed} of {data.total} are searchable.
        </span>{" "}
        Answers may miss newer ones for now.
        {busiest.length > 0 && (
          <span className="text-muted-foreground">
            {" "}
            ({busiest.map((s) => `${sourceLabel(s.source_id)} ${s.total - s.pending}/${s.total}`).join(", ")})
          </span>
        )}
      </p>
    </div>
  );
}

function Bubble({ message, pending }: { message: ChatMessage; pending: boolean }) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const cite = (n: number) => {
    setHighlighted(n);
    document.getElementById(`src-${message.id}-${n}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.setTimeout(() => setHighlighted(null), 2200);
  };

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-primary px-4 py-2.5 text-[15px] leading-relaxed text-primary-foreground">
          {message.content}
        </p>
      </div>
    );
  }
  return (
    <div className="flex gap-3">
      <span className="mt-1 flex size-8 shrink-0 items-center justify-center rounded-full border border-brass/60 bg-card font-serif text-base font-semibold text-brass">
        A
      </span>
      <div className={cn("min-w-0 flex-1 rounded-lg rounded-tl-sm border bg-card px-5 py-4", message.error ? "border-destructive/40" : "border-border")}>
        {message.content ? (
          <div className={message.error ? "text-destructive" : undefined}>
            <AnswerText text={message.content} onCite={cite} />
          </div>
        ) : (
          <p className="font-serif text-lg italic text-muted-foreground">{pending ? "Consulting the library…" : ""}</p>
        )}
        {pending && message.content && <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-brass align-middle" />}
        {message.general && message.content && (
          <p className="mt-3 border-t border-border pt-2 text-[11.5px] italic text-muted-foreground">
            General answer. Your documents were not searched.
          </p>
        )}
        <SourceCards messageId={message.id} sources={message.sources} highlighted={highlighted} />
      </div>
    </div>
  );
}

export function ChatView() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const refreshList = useCallback(() => chatApi.list().then(setConversations).catch(() => undefined), []);
  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, [messages]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const open = async (id: string) => {
    if (busy) return;
    try {
      const detail = await chatApi.get(id);
      setActiveId(id);
      setMessages(detail.messages);
    } catch {
      toast.error("Could not open that conversation.");
    }
  };

  const startNew = () => {
    if (busy) return;
    setActiveId(null);
    setMessages([]);
    textareaRef.current?.focus();
  };

  const remove = async (id: string) => {
    await chatApi.remove(id);
    if (id === activeId) startNew();
    void refreshList();
  };

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    const stamp = Date.now();
    const assistantId = `a-${stamp}`;
    setMessages((m) => [
      ...m,
      { id: `u-${stamp}`, role: "user", content: question, sources: [] },
      { id: assistantId, role: "assistant", content: "", sources: [] },
    ]);
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((all) => all.map((m) => (m.id === assistantId ? fn(m) : m)));

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of streamAnswer(question, activeId, controller.signal)) {
        if (event.type === "conversation") setActiveId(event.id);
        else if (event.type === "route") patch((m) => ({ ...m, general: event.route === "general" }));
        else if (event.type === "sources") patch((m) => ({ ...m, sources: event.sources }));
        else if (event.type === "token") patch((m) => ({ ...m, content: m.content + event.text }));
        else if (event.type === "done") patch((m) => ({ ...m, id: event.message_id, sources: event.sources }));
        else if (event.type === "error") patch((m) => ({ ...m, content: event.message, error: true }));
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        patch((m) => ({ ...m, content: m.content || "Something went wrong reaching the assistant.", error: true }));
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
      void refreshList();
    }
  };

  return (
    <div className="grid h-[calc(100vh-17.5rem)] min-h-[30rem] gap-6 md:grid-cols-[14.5rem_1fr]">
      <aside className="hidden min-h-0 flex-col md:flex">
        <Button variant="outline" size="sm" onClick={startNew} disabled={busy} className="mb-3 justify-start">
          <MessageSquarePlus /> New conversation
        </Button>
        <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">Earlier</p>
        <ul className="-mr-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-2">
          {conversations.length === 0 && <li className="py-2 text-[13px] italic text-muted-foreground">Nothing yet.</li>}
          {conversations.map((c) => (
            <li key={c.id} className="group relative">
              <button
                type="button"
                onClick={() => open(c.id)}
                className={cn(
                  "w-full rounded-md px-3 py-2 text-left transition-colors hover:bg-accent",
                  c.id === activeId && "bg-accent",
                )}
              >
                <span className="block truncate pr-5 text-[13px] font-medium">{c.title}</span>
                <span className="block text-[11px] text-muted-foreground">{timeAgo(c.updated_at)}</span>
              </button>
              <button
                type="button"
                onClick={() => remove(c.id)}
                aria-label={`Delete ${c.title}`}
                className="absolute right-1.5 top-2 rounded p-1 text-muted-foreground opacity-0 transition hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Trash2 className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-muted/40">
        <IndexingBanner />
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-6">
          {messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center">
              <BookOpenText className="mb-4 size-9 text-brass" strokeWidth={1.4} />
              <h2 className="font-serif text-3xl font-semibold">Ask your library</h2>
              <p className="mt-2 text-[14px] leading-relaxed text-muted-foreground">
                Answers are drawn only from the documents you have added, and every claim points to its source.
              </p>
              <div className="mt-6 flex flex-col gap-2 self-stretch">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => send(s)}
                    className="rounded-md border border-border bg-card px-4 py-2.5 text-left text-[13.5px] transition-colors hover:border-brass hover:bg-accent"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => <Bubble key={m.id} message={m} pending={busy && i === messages.length - 1} />)
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
          className="border-t border-border bg-card p-3"
        >
          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder="Ask a question about your documents…"
              maxLength={4000}
              className="max-h-40 min-h-10 flex-1 resize-none rounded-md border border-input bg-background px-3.5 py-2.5 text-[15px] outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            />
            {busy ? (
              <Button type="button" size="icon" variant="outline" onClick={() => abortRef.current?.abort()} aria-label="Stop">
                <Square className="fill-current" />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!input.trim()} aria-label="Send">
                <ArrowUp />
              </Button>
            )}
          </div>
          <p className="mt-1.5 px-1 text-[11px] text-muted-foreground">Enter to send · Shift+Enter for a new line</p>
        </form>
      </section>
    </div>
  );
}
