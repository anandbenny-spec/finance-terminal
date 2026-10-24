// VoteGrid (Design 01 §1.14, §4.5): the vote table, and the dot plot's table view when it comes (Pass 1b). Member ·
// vote (bps) · stance vote (RBI) · dissent. A dissent shows as a "Dissent" tag and the member's stated preference
// ("preferred −50 bps"), never as a colour. The tally line sits above it (lib/policy/decisions.ts).
import type { VoteRow } from "@/lib/policy/meeting";

export function VoteGrid({ rows, stance, caption }: { rows: VoteRow[]; stance: boolean; caption: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-raised">
          <tr>
            {["Member", "Vote (bps)", ...(stance ? ["Stance vote"] : []), "Dissent"].map((h, i) => (
              <th
                key={h}
                scope="col"
                className={`h-8 border-b border-line-strong px-3 text-meta font-medium whitespace-nowrap text-ink-2 ${i === 1 ? "text-right" : "text-left"}`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="h-8 border-b border-line last:border-b-0">
              <th scope="row" className="px-3 text-left font-normal whitespace-nowrap text-ink-1">
                {r.member}
              </th>
              <td className="px-3 text-right whitespace-nowrap">{r.vote}</td>
              {stance && <td className="px-3 whitespace-nowrap">{r.stance}</td>}
              <td className="px-3">
                {r.dissent && (
                  <span className="inline-flex flex-wrap items-center gap-1.5">
                    <span className="rounded-sm border border-line-strong px-1.5 text-meta text-ink-2">Dissent</span>
                    {r.dissent}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
