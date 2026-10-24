// The meeting page's document parts rendered to HTML as the server sends them (C4): ¶ anchors that ¶ links land on,
// the cited span in <mark>, the redline's <ins>/<del> with "Changes only" folding, and the vote table's dissents in
// words. Test text, not real documents.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DocumentView } from "@/components/ai/DocumentView";
import { Redline } from "@/components/ai/Redline";
import { VoteGrid } from "@/components/ai/VoteGrid";
import { buildRedline, parsePairs } from "@/lib/policy/redline";
import { paragraphs } from "@/lib/policy/text";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

describe("DocumentView", () => {
  const paras = paragraphs("The Committee met.\n\nIt decided to lower the range.\n\nVoting for the action were A, B.");

  it("gives every paragraph its ¶ anchor and link", () => {
    const out = renderToStaticMarkup(<DocumentView paragraphs={paras} label="FOMC statement" />);
    expect(out).toContain('id="p-1"');
    expect(out).toContain('id="p-3"');
    expect(out).toContain('href="#p-2"');
    expect(text(out)).toContain("It decided to lower the range. ¶2");
  });

  it("marks a cited span by code points, and adds named anchors", () => {
    const start = paras[1].start + 14; // "lower the range"
    const out = renderToStaticMarkup(<DocumentView paragraphs={paras} mark={[start, start + 15]} anchors={{ 3: "member-1" }} label="x" />);
    expect(out).toContain('<mark id="cited"><span>lower the range</span></mark>');
    expect(out).toContain('id="member-1"');
  });
});

describe("Redline", () => {
  const model = buildRedline(
    parsePairs({
      version: 2,
      pairs: [
        { kind: "same", text: "Unchanged one.", new_para: [1], old_para: [1] },
        { kind: "same", text: "Unchanged two.", new_para: [2], old_para: [2] },
        { kind: "changed", new_para: [3], old_para: [3], ops: [{ op: "eq", text: "Decided to " }, { op: "del", text: "hold" }, { op: "ins", text: "cut" }, { op: "eq", text: "." }] },
      ],
    })!,
  );

  it("shows the changes with <ins> and <del>, the counts, and folds unchanged paragraphs by default", () => {
    const out = renderToStaticMarkup(<Redline model={model} added={1} removed={1} />);
    expect(out).toContain("<del>hold</del>");
    expect(out).toContain("<ins>cut</ins>");
    expect(text(out)).toContain("+1 / −1 words");
    expect(text(out)).toContain("2 unchanged paragraphs");
    expect(out).not.toContain("Unchanged one.");
  });
});

describe("VoteGrid", () => {
  it("shows a dissent as a tag and the member's preference, never a colour", () => {
    const out = renderToStaticMarkup(
      <VoteGrid
        rows={[
          { key: "a", member: "A", vote: "−25", stance: null, dissent: null },
          { key: "z", member: "Z", vote: "−50", stance: null, dissent: "preferred −50 bps" },
        ]}
        stance={false}
        caption="Votes"
      />,
    );
    expect(text(out)).toContain("Z −50 Dissent preferred −50 bps");
    expect(out).not.toMatch(/text-(up|down)|bg-(up|down)/);
  });
});
