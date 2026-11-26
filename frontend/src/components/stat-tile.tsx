import { cn } from "@/lib/utils";

export function StatTile({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: string; tone?: "alert" }) {
  return (
    <div className="rounded-lg border bg-card px-5 py-4 shadow-[0_1px_2px_oklch(0.3_0.05_90/0.06)]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{label}</p>
      <p className={cn("mt-2 font-serif text-4xl font-semibold leading-none [font-variant-numeric:lining-nums]", tone === "alert" && "text-destructive")}>{value}</p>
      {hint && <p className="mt-2 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
