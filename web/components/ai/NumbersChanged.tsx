// NumbersChanged (Design 01 §1.16 layer 2, §4.5): item · previous (date) · new (date) · change. Unchanged items fold
// into one text-meta line. Changes carry a sign and no colour: a revision isn't a market move. Values are formatted
// by lib/policy/numbers.ts (lib/format.ts); the stance carries its "AI-extracted" qualifier.
import { Qualifier } from "@/components/data/Qualifier";
import { formatDate } from "@/lib/format";
import type { NumberItem } from "@/lib/policy/numbers";

export function NumbersChanged({ items, previousDate, newDate }: { items: NumberItem[]; previousDate: string; newDate: string }) {
  const changed = items.filter((x) => x.changed);
  const unchanged = items.filter((x) => !x.changed);
  return (
    <div>
      {changed.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-data">
            <caption className="sr-only">Numbers that changed against the previous decision</caption>
            <thead className="bg-raised">
              <tr>
                <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium whitespace-nowrap text-ink-2">Item</th>
                <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium whitespace-nowrap text-ink-2">
                  Previous ({formatDate(previousDate)})
                </th>
                <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium whitespace-nowrap text-ink-2">
                  New ({formatDate(newDate)})
                </th>
                <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium whitespace-nowrap text-ink-2">Change</th>
              </tr>
            </thead>
            <tbody>
              {changed.map((x) => (
                <tr key={x.key} className="h-8 border-b border-line last:border-b-0">
                  <th scope="row" className="px-3 text-left font-normal text-ink-1">
                    {x.label}
                    {x.qualifier && <Qualifier className="ml-1.5 align-middle">{x.qualifier}</Qualifier>}
                  </th>
                  <td className="px-3 text-right whitespace-nowrap">{x.previous}</td>
                  <td className="px-3 text-right whitespace-nowrap">{x.next}</td>
                  <td className="px-3 text-right whitespace-nowrap">{x.change ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-3 py-2.5 text-data text-ink-1">None of the numbers below changed.</p>
      )}
      {unchanged.length > 0 && (
        <p className="px-3 py-2 text-meta text-ink-3">
          {changed.length > 0 ? "Unchanged" : "Compared"}: {unchanged.map((x) => `${x.label} (${x.next})`).join(" · ")}
        </p>
      )}
    </div>
  );
}
