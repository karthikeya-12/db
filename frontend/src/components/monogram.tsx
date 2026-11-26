import { cn } from "@/lib/utils";

/** A provider's initial inside a brass ring: logos would need external requests, this keeps the page self-contained. */
export function Monogram({ name, muted }: { name: string; muted?: boolean }) {
  return (
    <div
      className={cn(
        "flex size-11 shrink-0 items-center justify-center rounded-full border font-serif text-2xl font-semibold",
        muted ? "border-border text-muted-foreground" : "border-brass/60 bg-accent text-primary",
      )}
      aria-hidden
    >
      {name.charAt(0)}
    </div>
  );
}
