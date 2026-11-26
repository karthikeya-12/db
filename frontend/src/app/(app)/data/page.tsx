import { LibraryView } from "@/components/library-view";
import { PageHeader } from "@/components/page-header";

export default function Page() {
  return (
    <>
      <PageHeader eyebrow="Collection" title="Library" description="Documents you have uploaded or that your connectors have brought in." />
      <LibraryView />
    </>
  );
}
