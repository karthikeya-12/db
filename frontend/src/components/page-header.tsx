export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-9">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          {eyebrow && (
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-brass">{eyebrow}</p>
          )}
          <h1 className="text-[2.75rem] font-semibold leading-none tracking-tight">{title}</h1>
          {description && <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted-foreground">{description}</p>}
        </div>
        {actions}
      </div>
      <div className="mt-6 flex items-center gap-3 text-brass/70" aria-hidden>
        <span className="h-px flex-1 bg-border" />
        <span className="size-1.5 rotate-45 border border-brass/70" />
        <span className="h-px w-10 bg-border" />
      </div>
    </header>
  );
}
