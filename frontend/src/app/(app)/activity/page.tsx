import { LedgerView } from "@/components/ledger-view";
import { PageHeader } from "@/components/page-header";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Records" title="Ledger" description="Every ingestion run, and anything that needs a second look." />
      <LedgerView />
    </>
  );
}
