// DocumentView (Design 01 §4.5, §5.3): a document's canonical text in the reading layout (68 ch, read size), one
// block per paragraph with its ¶ anchor (#p-3) and a ¶ link beside it, so every ¶ link lands on its paragraph. A
// cited span (code-point offsets, as stored) is wrapped in <mark>. Named anchors (#member-…) mark where each
// member's statement starts in minutes. The text is the chunks' stored bodies; nothing here rewrites it.
import type { ReactNode } from "react";
import { sliceCodePoints, type Paragraph } from "@/lib/policy/text";

function lines(text: string): ReactNode {
  const parts = text.split("\n");
  return parts.map((line, i) => (
    <span key={i}>
      {i > 0 && <br />}
      {line}
    </span>
  ));
}

function body(p: Paragraph, mark: [number, number] | null): ReactNode {
  if (!mark || mark[1] <= p.start || mark[0] >= p.end) return lines(p.text);
  const from = Math.max(mark[0], p.start) - p.start;
  const to = Math.min(mark[1], p.end) - p.start;
  return (
    <>
      {lines(sliceCodePoints(p.text, 0, from))}
      <mark id={mark[0] >= p.start ? "cited" : undefined}>{lines(sliceCodePoints(p.text, from, to))}</mark>
      {lines(sliceCodePoints(p.text, to, [...p.text].length))}
    </>
  );
}

export function DocumentView({
  paragraphs,
  mark = null,
  anchors = {},
  label,
}: {
  paragraphs: Paragraph[];
  /** A cited span to highlight, [start, end) in code points. */
  mark?: [number, number] | null;
  /** Extra anchor ids by ¶ number (minutes: each member's first paragraph). */
  anchors?: Record<number, string>;
  label: string;
}) {
  return (
    <article aria-label={label} className="max-w-(--reading-width) text-read text-ink-1">
      {paragraphs.map((p) => (
        <div key={p.n} id={anchors[p.n]} className="group relative scroll-mt-24">
          <p id={`p-${p.n}`} className="mb-4 scroll-mt-24 break-words">
            {body(p, mark)}{" "}
            <a href={`#p-${p.n}`} className="font-mono text-meta whitespace-nowrap text-ink-3 no-underline hover:text-ink-1" aria-label={`Paragraph ${p.n}`}>
              ¶{p.n}
            </a>
          </p>
        </div>
      ))}
    </article>
  );
}
