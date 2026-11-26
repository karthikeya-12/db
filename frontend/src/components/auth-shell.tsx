import { Brand } from "@/components/sidebar";

/** The two-column frame shared by the sign-in and sign-up pages. */
export function AuthShell({ eyebrow, title, subtitle, children }: { eyebrow: string; title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen md:grid-cols-[1.05fr_1fr]">
      <aside className="relative hidden flex-col justify-between bg-sidebar p-12 text-sidebar-foreground md:flex">
        <Brand />
        <blockquote className="max-w-md">
          <p className="font-serif text-4xl font-medium italic leading-snug">
            &ldquo;A library is not a luxury but one of the necessities of life.&rdquo;
          </p>
          <footer className="mt-5 text-[11px] font-semibold uppercase tracking-[0.24em] text-brass">Henry Ward Beecher</footer>
        </blockquote>
        <p className="text-xs text-sidebar-muted">Everything you know, in one quiet place.</p>
        <div className="pointer-events-none absolute inset-6 border border-brass/25" aria-hidden />
      </aside>
      <main className="flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-sm">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-brass">{eyebrow}</p>
          <h1 className="text-[2.75rem] font-semibold leading-none tracking-tight">{title}</h1>
          <p className="mt-3 text-[15px] text-muted-foreground">{subtitle}</p>
          {children}
        </div>
      </main>
    </div>
  );
}
