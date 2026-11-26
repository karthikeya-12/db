import { MobileNav, Sidebar } from "@/components/sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav />
        <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10 md:px-10">{children}</main>
      </div>
    </div>
  );
}
