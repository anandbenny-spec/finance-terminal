"use client";

// ValueChainFlow (VC-06, #191; Spec 02B §12.7; Anand's choice, 29 Oct 2026, about 19:00 IST): suppliers → the company → customers,
// with the related-party shares of purchases and sales below the company. Inline SVG arrows and a CSS grid, no chart library.
// Every arrow is the same width (decision 26): the figures carry the values, so no length or thickness is read as one. Tokens
// only: the company's box takes the India colour (violet, series-2); no green or red, because a share is not a market move
// (Design 01 §1.2). A held or absent value shows its short state in the item itself ("Not reported"); the full sentence is in the
// panel's explainer. "View as table" swaps the diagram for the same figures as a table (Design 01 §5). At 320 px the columns stack,
// suppliers then the company then customers, and the arrows point down. The diagram is hidden from screen readers in favour of
// one paragraph that reads the flow as sentences. It formats nothing: it takes the strings lib/equity/valuechain.ts built.
import { ArrowRight, ArrowDown, Table2, Workflow } from "lucide-react";
import { useState } from "react";
import type { Flow, FlowItem } from "@/lib/equity/valuechain";

/** The one arrow: same size and same stroke wherever it sits; it points right from md, down below. */
function Arrow() {
  return (
    <div aria-hidden className="flex items-center justify-center py-1 text-ink-3 md:py-0" data-flow-arrow>
      <ArrowRight className="hidden size-6 md:block" strokeWidth={2} />
      <ArrowDown className="size-6 md:hidden" strokeWidth={2} />
    </div>
  );
}

function Item({ item }: { item: FlowItem }) {
  return (
    <li className="rounded-md border border-line-strong bg-raised px-3 py-2" data-flow-item={item.key}>
      <span className="block text-meta text-ink-2">{item.label}</span>
      <span className={`block text-data ${item.isState ? "text-ink-2" : "text-ink-1"}`}>{item.display}</span>
      {item.details.map((d) => (
        <span key={d} className="block text-meta text-ink-2">
          {d}
        </span>
      ))}
      {item.change && <span className="block text-meta text-ink-3">{item.change}</span>}
      {item.restated && <span className="block text-meta text-ink-3">{item.restated}</span>}
    </li>
  );
}

function Column({ title, items, empty }: { title: string; items: FlowItem[]; empty: string }) {
  return (
    <div className="min-w-0">
      <h4 className="mb-2 text-meta font-medium text-ink-2">{title}</h4>
      {items.length === 0 ? (
        <p className="rounded-md border border-dashed border-line-strong px-3 py-2 text-data text-ink-2">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((i) => (
            <Item key={i.key} item={i} />
          ))}
        </ul>
      )}
    </div>
  );
}

function FlowTable({ flow }: { flow: Flow }) {
  return (
    <div className="overflow-x-auto" data-flow-table>
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">{`Value chain, ${flow.fy}, ${flow.boundary}`}</caption>
        <thead className="bg-raised">
          <tr>
            {["Where", "Figure", "Value", "BRSR item"].map((h, i) => (
              <th key={h} scope="col" className={`h-8 border-b border-line-strong px-3 text-meta font-medium whitespace-nowrap text-ink-2 ${i === 2 ? "text-right" : "text-left"}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {flow.table.map((r) => (
            <tr key={`${r.side}|${r.label}`} className="border-b border-line last:border-b-0">
              <td className="px-3 py-1.5 whitespace-nowrap text-ink-2">{r.side}</td>
              <th scope="row" className="px-3 py-1.5 text-left font-normal text-ink-1">
                {r.label}
              </th>
              <td className="px-3 py-1.5 text-right whitespace-nowrap">{r.value}</td>
              <td className="px-3 py-1.5 text-ink-2">{r.itemRef}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ValueChainFlow({ flow }: { flow: Flow }) {
  const [asTable, setAsTable] = useState(false);
  const label = asTable ? "View as diagram" : "View as table";
  return (
    <div>
      <div className="mb-2 flex items-start justify-between gap-3">
        <p className="text-data text-ink-1">
          {flow.fy} <span aria-hidden>·</span> <span className="text-ink-2">{flow.boundary}</span>
        </p>
        <button
          type="button"
          onClick={() => setAsTable((v) => !v)}
          aria-pressed={asTable}
          aria-label={label}
          title={label}
          className="grid size-7 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
        >
          {asTable ? <Workflow aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
        </button>
      </div>
      <p className="sr-only" data-flow-aria>
        {flow.aria}
      </p>
      {asTable ? (
        <FlowTable flow={flow} />
      ) : (
        <div aria-hidden className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center md:gap-3" data-flow-diagram>
          <Column title="Suppliers" items={flow.suppliers} empty="Not reported" />
          <Arrow />
          <div className="min-w-0 rounded-md border border-series-2 bg-panel px-3 py-2" data-flow-company>
            <h4 className="text-data font-semibold text-ink-1">{flow.company}</h4>
            {flow.products.length > 0 ? (
              <ul className="mt-1 flex flex-col gap-0.5">
                {flow.products.map((p) => (
                  <li key={p.name} className="flex justify-between gap-3 text-data text-ink-1">
                    <span className="min-w-0 break-words">{p.name}</span>
                    <span className="shrink-0">{p.share}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-meta text-ink-2">Products: {flow.productsState}</p>
            )}
            <p className="mt-1 text-meta text-ink-2">Products, share of turnover</p>
          </div>
          <Arrow />
          <Column title="Customers" items={flow.customers} empty="Not reported" />
        </div>
      )}
      {!asTable && (
        <div aria-hidden className="mt-3" data-flow-group>
          <Column title="Within the group" items={flow.group} empty="Not reported" />
        </div>
      )}
    </div>
  );
}
