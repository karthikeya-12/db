import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-center">
      <p className="text-sm text-muted-foreground">That page does not exist.</p>
      <Link href="/data" className="text-sm font-medium text-primary hover:underline">
        Back to Data
      </Link>
    </div>
  );
}
