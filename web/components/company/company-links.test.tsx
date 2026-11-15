// Company links don't prefetch (#468; recommendation 1 of #465). Since #454 every company URL is a static, cached route, and a
// prefetch of an uncached one runs that company's whole render, so a visitor's first page started dozens of renders at once.
// This guard fails when a <Link> to a company page loses `prefetch={false}`, and checks the two shared parts (Tabs, PageHeader)
// that build their links from a caller's items.
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

const ROOT = join(__dirname, "..", "..");

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
function openingTag(src: string, start: number): string {
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

const COMPANY_HREF = /companyHref\(|\/companies\/|callHref\(|callsTabHref\(|rateHref\(|valueChainHref\(|yearHref\(/;

describe("links to a company page", () => {
  const files = [...sourceFiles(join(ROOT, "components")), ...sourceFiles(join(ROOT, "app"))];

  it("finds the files to scan", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("every <Link> to a company page has prefetch={false}", () => {
    const missing: string[] = [];
    let seen = 0;
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      for (const m of src.matchAll(/<Link\b/g)) {
        const tag = openingTag(src, m.index!);
        const href = /\bhref=(\{[\s\S]*\}|"[^"]*")/.exec(tag)?.[0] ?? "";
        if (!COMPANY_HREF.test(href)) continue;
        seen++;
        if (!/\bprefetch=\{false\}/.test(tag)) {
          const line = src.slice(0, m.index).split("\n").length;
          missing.push(`${file.slice(ROOT.length + 1)}:${line}`);
        }
      }
    }
    expect(seen).toBeGreaterThan(10);
    expect(missing).toEqual([]);
  });

  it("CompanyView passes prefetch={false} to Tabs, and the company crumbs carry prefetch: false", () => {
    const view = readFileSync(join(ROOT, "components/company/CompanyView.tsx"), "utf8");
    expect(/<Tabs\s+prefetch=\{false\}/.test(view)).toBe(true);
    for (const f of ["components/policy/DocumentPage.tsx", "components/company/value-chain/ValueChainPage.tsx", "components/company/us/ValueChainNotCovered.tsx"]) {
      const src = readFileSync(join(ROOT, f), "utf8");
      expect(src, f).toMatch(/href: `\/companies\/\$\{[^}]+\}`, prefetch: false/);
    }
  });
});

describe("Tabs", () => {
  const items = [
    { id: "a", label: "A", href: "/x/a" },
    { id: "b", label: "B", href: "/x/b" },
  ];
  it("leaves prefetch unset when the caller doesn't pass it", () => {
    const out = renderToStaticMarkup(<Tabs label="T" items={items} current="a" />);
    expect(out.match(/data-prefetch="absent"/g)).toHaveLength(2);
  });
  it("passes prefetch={false} to every link", () => {
    const out = renderToStaticMarkup(<Tabs label="T" items={items} current="a" prefetch={false} />);
    expect(out.match(/data-prefetch="false"/g)).toHaveLength(2);
  });
});

describe("PageHeader crumbs", () => {
  it("leaves prefetch unset unless the crumb sets it", () => {
    const out = renderToStaticMarkup(
      <PageHeader crumbs={[{ label: "One", href: "/one" }, { label: "Two", href: "/two", prefetch: false }, "End"]} title="T" />,
    );
    expect(out).toContain('href="/one" data-prefetch="absent"');
    expect(out).toContain('href="/two" data-prefetch="false"');
  });
});
