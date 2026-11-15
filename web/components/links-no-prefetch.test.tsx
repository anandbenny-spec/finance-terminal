// No link preloads another page (#592; #468 did it for company links). A prefetch of an uncached route runs that page's reads,
// and a visitor's sidebar in view started up to eight renders at once. This guard fails when any <Link> loses `prefetch={false}`
// (file and line named). Tabs and PageHeader build their links from a caller's items and default to false: checked by rendering.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PageHeader } from "@/components/data/PageHeader";
import { Tabs } from "@/components/data/Tabs";

vi.mock("next/link", () => ({
  default: (props: Record<string, unknown>) => {
    const { href, prefetch, children, ...rest } = props as { href: string; prefetch?: boolean; children?: unknown };
    return (
      <a href={href} data-prefetch={prefetch === undefined ? "absent" : String(prefetch)} {...(rest as object)}>
        {children as never}
      </a>
    );
  },
}));

const ROOT = join(__dirname, "..");
const BUILT_FROM_ITEMS = ["components/data/Tabs.tsx", "components/data/PageHeader.tsx"];

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sourceFiles(p));
    else if (p.endsWith(".tsx") && !/\.test\.tsx$/.test(p)) out.push(p);
  }
  return out;
}

/** The opening tag of the <Link starting at `start`: up to its closing `>`, skipping `=>` and anything inside braces or backticks. */
export function openingTag(src: string, start: number): string {
  let depth = 0;
  let inTemplate = false;
  for (let i = start; i < src.length; i++) {
    const ch = src[i];
    if (inTemplate) {
      if (ch === "`") inTemplate = false;
      continue;
    }
    if (ch === "`") inTemplate = true;
    else if (ch === "{") depth++;
    else if (ch === "}") depth--;
    else if (ch === ">" && depth === 0 && src[i - 1] !== "=") return src.slice(start, i + 1);
  }
  return src.slice(start);
}

/** `file:line` of every <Link> in `src` without `prefetch={false}`. */
export function linksMissingPrefetch(src: string, name: string): string[] {
  const missing: string[] = [];
  for (const m of src.matchAll(/<Link\b/g)) {
    if (!/\bprefetch=\{false\}/.test(openingTag(src, m.index!))) missing.push(`${name}:${src.slice(0, m.index).split("\n").length}`);
  }
  return missing;
}

describe("every <Link>", () => {
  const files = [...sourceFiles(join(ROOT, "components")), ...sourceFiles(join(ROOT, "app"))];

  it("has prefetch={false}", () => {
    const missing: string[] = [];
    let seen = 0;
    for (const file of files) {
      const rel = file.slice(ROOT.length + 1);
      if (BUILT_FROM_ITEMS.includes(rel)) continue;
      const src = readFileSync(file, "utf8");
      seen += [...src.matchAll(/<Link\b/g)].length;
      missing.push(...linksMissingPrefetch(src, rel));
    }
    expect(seen).toBeGreaterThan(30);
    expect(missing).toEqual([]);
  });

  it("is caught by the guard when one link loses the prop", () => {
    expect(linksMissingPrefetch('a\n<Link href="/x" prefetch={false}>ok</Link>\n<Link href="/y">no</Link>', "f.tsx")).toEqual(["f.tsx:3"]);
  });
});

describe("Tabs and PageHeader", () => {
  it("render data-prefetch=false with no prefetch passed", () => {
    const items = [
      { id: "a", label: "A", href: "/x/a" },
      { id: "b", label: "B", href: "/x/b" },
    ];
    const tabs = renderToStaticMarkup(<Tabs label="T" items={items} current="a" />);
    expect(tabs.match(/data-prefetch="false"/g)).toHaveLength(2);
    expect(tabs).not.toContain('data-prefetch="absent"');
    const head = renderToStaticMarkup(<PageHeader crumbs={[{ label: "One", href: "/one" }, "End"]} title="T" />);
    expect(head).toContain('data-prefetch="false"');
  });
});
