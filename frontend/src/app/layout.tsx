import type { Metadata } from "next";
import "@fontsource-variable/cormorant";
import "@fontsource-variable/hanken-grotesk";
import { Toaster } from "sonner";
import "./globals.css";

export const metadata: Metadata = {
  title: "The Archive",
  description: "A private library for everything you know. Connect, ingest, and consult.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">
        {children}
        <Toaster position="bottom-right" />
      </body>
    </html>
  );
}
