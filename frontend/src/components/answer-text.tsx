import { Fragment } from "react";

/** Renders an assistant answer: paragraphs, simple lists, **bold**, and [n] citations as small brass markers. */
function Inline({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  return (
    <>
      {text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g).map((part, i) => {
        const cite = /^\[(\d+)\]$/.exec(part);
        if (cite) {
          const n = Number(cite[1]);
          return (
            <button
              key={i}
              type="button"
              onClick={() => onCite(n)}
              className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-0.5 items-center justify-center rounded-sm border border-brass/60 bg-brass/10 px-1 text-[11px] font-semibold leading-none text-brass transition-colors hover:bg-brass hover:text-primary-foreground"
              aria-label={`Source ${n}`}
            >
              {n}
            </button>
          );
        }
        if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

export function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const blocks: { kind: "p" | "ul" | "ol"; lines: string[] }[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      continue;
    }
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    const kind = bullet ? "ul" : numbered ? "ol" : "p";
    const content = bullet?.[1] ?? numbered?.[1] ?? line;
    const last = blocks[blocks.length - 1];
    if (kind !== "p" && last?.kind === kind) last.lines.push(content);
    else blocks.push({ kind, lines: [content] });
  }
  return (
    <div className="space-y-3 text-[15px] leading-relaxed">
      {blocks.map((block, i) =>
        block.kind === "p" ? (
          <p key={i}>
            <Inline text={block.lines[0]} onCite={onCite} />
          </p>
        ) : (
          (() => {
            const List = block.kind;
            return (
              <List key={i} className={`space-y-1 pl-5 ${block.kind === "ul" ? "list-disc" : "list-decimal"} marker:text-brass`}>
                {block.lines.map((l, j) => (
                  <li key={j}>
                    <Inline text={l} onCite={onCite} />
                  </li>
                ))}
              </List>
            );
          })()
        ),
      )}
    </div>
  );
}
