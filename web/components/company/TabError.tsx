// TabError (#328): the body of a company-page tab whose own reads failed or returned nothing. The header and the other
// tabs still work, so only this tab says "Couldn't load this panel. Try again." (a StateNote, never a blank body: CLAUDE.md
// §5). The reason goes to the server log, never to the visitor (Arch 02).
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";

export function TabError({ title, titleId }: { title: string; titleId: string }) {
  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <Panel title={title} titleId={titleId}>
        <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
      </Panel>
    </div>
  );
}
