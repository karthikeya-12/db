import { ConnectorsView } from "@/components/connectors-view";
import { PageHeader } from "@/components/page-header";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Sources" title="Connectors" description="Connect the places your knowledge lives. New data is brought in automatically." />
      <ConnectorsView />
    </>
  );
}
