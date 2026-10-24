// The design base (C1): globals.css and fonts.ts as Design 01 §2.6–§2.7, compiled by the installed Tailwind, and
// shadcn's primitives restyled by the tokens alone (Design 01 §10.3). A colour here changes only after Design 01
// §8's contrast check is re-run: these values are copied from Design 01 §2.1–§2.3, not from globals.css.
import { readdirSync } from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { WEB, loadDesignSystem, normalise, readWeb, rootVariables } from "./css";

const DESIGN_TOKENS: Record<string, string> = {
  // §2.1 surfaces, lines and ink
  "--canvas": "#08090b",
  "--panel": "#12161b",
  "--raised": "#1a1f26",
  "--active": "#232931",
  "--line": "#252a31",
  "--line-strong": "#343b45",
  "--line-input": "#646c78",
  "--ink-1": "#e6e8eb",
  "--ink-2": "#afb5be",
  "--ink-3": "#8c939e",
  // §2.2 accent, direction, status, redline
  "--amber": "#edb84a",
  "--amber-ink": "#111418",
  "--amber-wash": "rgb(237 184 74 / 0.12)",
  "--up": "#45c16a",
  "--down": "#f26b6b",
  "--flat": "var(--ink-2)",
  "--ok": "var(--up)",
  "--warn": "#f0913e",
  "--error": "var(--down)",
  "--ins-ink": "#8fdda4",
  "--ins-wash": "rgb(69 193 106 / 0.14)",
  "--del-ink": "#f59a9a",
  "--del-wash": "rgb(242 107 107 / 0.14)",
  // §2.3 chart palette
  "--series-1": "#1aa3b5",
  "--series-2": "#9085e9",
  "--series-3": "#d55181",
  "--band-alpha": "0.12",
  "--market-edge": "3px",
};

// §2.6's shadcn role mapping: what shadcn's primitives read.
const SHADCN_ROLES: Record<string, string> = {
  "--background": "var(--canvas)",
  "--foreground": "var(--ink-1)",
  "--card": "var(--panel)",
  "--card-foreground": "var(--ink-1)",
  "--popover": "var(--raised)",
  "--popover-foreground": "var(--ink-1)",
  "--primary": "var(--amber)",
  "--primary-foreground": "var(--amber-ink)",
  "--secondary": "var(--raised)",
  "--secondary-foreground": "var(--ink-1)",
  "--muted": "var(--raised)",
  "--muted-foreground": "var(--ink-2)",
  "--accent": "var(--active)",
  "--accent-foreground": "var(--ink-1)",
  "--destructive": "var(--down)",
  "--border": "var(--line)",
  "--input": "var(--line-input)",
  "--ring": "var(--amber)",
  "--radius": "4px",
  "--chart-1": "var(--series-1)",
  "--chart-2": "var(--series-2)",
  "--chart-3": "var(--series-3)",
};

const globals = readWeb("app/globals.css");
const code = globals.replace(/\/\*[\s\S]*?\*\//g, ""); // without its comments
const root = rootVariables(globals);
let ds: Awaited<ReturnType<typeof loadDesignSystem>>;

beforeAll(async () => {
  ds = await loadDesignSystem();
});

describe("globals.css: the tokens (Design 01 §2.6)", () => {
  it("declares every token at its Design 01 value", () => {
    for (const [name, value] of Object.entries(DESIGN_TOKENS)) expect(root.get(name), name).toBe(value);
  });

  it("maps shadcn's roles onto the tokens", () => {
    for (const [name, value] of Object.entries(SHADCN_ROLES)) expect(root.get(name), name).toBe(value);
  });

  it("has replaced shadcn init's colour variables: no oklch palette, no .dark block, no sidebar roles", () => {
    expect(code).not.toMatch(/oklch\(/);
    expect(code).not.toMatch(/^\s*\.dark\s*\{/m);
    expect(code).not.toMatch(/--sidebar(:|-(?!w:))|--chart-[45]/); // --sidebar-w is ours (Design 01 §2.5)
  });

  it("keeps the @import lines shadcn init added", () => {
    const imports = globals.match(/^@import .+;$/gm);
    expect(imports).toEqual(['@import "tailwindcss";', '@import "tw-animate-css";', '@import "shadcn/tailwind.css";']);
  });

  it("is dark only", () => {
    expect(code).toMatch(/:root \{\s*color-scheme: dark;/);
    expect(code).not.toMatch(/prefers-color-scheme/);
  });
});

describe("globals.css compiled by Tailwind", () => {
  it("resolves every utility Design 01 §2.6 names", () => {
    const named = ["bg-panel", "text-ink-3", "text-data", "rounded-md", "font-mono", "bg-primary", "bg-accent",
      "text-up", "bg-series-1", "bg-amber-wash"];
    const colourTokens = Object.keys(DESIGN_TOKENS)
      .filter((n) => !["--band-alpha", "--market-edge"].includes(n))
      .map((n) => `bg-${n.slice(2)}`);
    const roles = ["meta", "data", "ui", "read", "title", "page", "figure"].map((r) => `text-${r}`);
    const all = [...named, ...colourTokens, ...roles];
    const css = ds.candidatesToCss(all);
    all.forEach((c, i) => expect(css[i], c).not.toBeNull());
  });

  it("sizes type by role as Design 01 §2.4 sets it (nothing below 12 px)", () => {
    const px = (rem: string | undefined) => Number(rem?.replace("rem", "")) * 16;
    const roles: Record<string, [number, number]> = {
      meta: [12, 16], data: [13, 18], ui: [14, 20], read: [16, 26], title: [18, 26], page: [22, 28], figure: [28, 34],
    };
    for (const [role, [size, lineHeight]] of Object.entries(roles)) {
      expect(px(ds.resolveThemeValue(`--text-${role}`)), role).toBe(size);
      expect(px(ds.resolveThemeValue(`--text-${role}--line-height`)), role).toBe(lineHeight);
    }
  });

  it("holds every radius step at 2 px or 4 px (Design 01 §2.5), including the ones shadcn's Nova style uses", () => {
    const steps = ["xs", "sm", "md", "lg", "xl", "2xl", "3xl", "4xl"];
    for (const step of steps) {
      const value = ds.resolveThemeValue(`--radius-${step}`);
      expect(["2px", "4px", "0.125rem"], `--radius-${step} = ${value}`).toContain(value);
    }
  });

  it("uses Plex for both faces, through next/font's variables (app/fonts.ts)", () => {
    expect(ds.resolveThemeValue("--font-sans")).toMatch(/^var\(--font-plex-sans\),/);
    expect(ds.resolveThemeValue("--font-mono")).toMatch(/^var\(--font-plex-mono\),/);
  });

  it("makes `dark:` a class variant (layout.tsx's `dark`), not the viewer's OS setting", () => {
    const [css] = ds.candidatesToCss(["dark:bg-input/30"]);
    expect(css).toContain(":is(.dark *)");
    expect(css).not.toContain("prefers-color-scheme");
  });
});

// Design 01 §10.3: shadcn's primitives need no colour edits. Every class they use that sets a colour must come from
// a token: a colour utility with no token (bg-sidebar) compiles to nothing, and Tailwind's own palette
// (bg-red-500) or a literal colour would bypass the tokens. Our own components (C2 on) are held to the same rule,
// and to CLAUDE.md §5's "no arbitrary values": a size comes from Tailwind's scale or a token variable that
// globals.css declares (`h-(--topbar-h)`), never a bracketed value (`h-[48px]`).
const COLOUR_PREFIX = /^!?-?(bg|text|border(-[xytrblse])?|ring(-offset)?|outline|fill|stroke|decoration|divide|shadow|caret|accent|placeholder|from|via|to)-/;
const LITERAL = /#[0-9a-f]{3,8}\b|\b(rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(/i;

function classesIn(source: string): string[] {
  const strings = source.match(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g) ?? [];
  return [...new Set(strings.flatMap((s) => s.slice(1, -1).split(/\s+/)).filter(Boolean))];
}

/** The utility without its variants (`dark:hover:bg-x/50` → `bg-x/50`); brackets may hold colons. */
function utilityOf(candidate: string): string {
  const parts = candidate.split(/:(?![^[]*\])/);
  return parts[parts.length - 1];
}

function componentFiles(dir: string): string[] {
  return readdirSync(path.join(WEB, dir), { recursive: true, encoding: "utf8" })
    .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
    .map((f) => `${dir}/${f}`);
}

/** Colour classes that don't come from a token, in a file's class strings. */
function colourProblems(file: string): string[] {
  const candidates = classesIn(readWeb(file));
  const css = ds.candidatesToCss(candidates);
  const problems: string[] = [];
  candidates.forEach((c, i) => {
    const out = css[i];
    if (out === null) {
      // Not a Tailwind class at all is fine (a prop value, `group/button`); a colour class that doesn't compile isn't.
      if (COLOUR_PREFIX.test(utilityOf(c))) problems.push(`${c}: no such colour`);
      return;
    }
    // Tailwind's own @property defaults (`initial-value: 0 0 #0000`, transparent) aren't the primitive's colours.
    const rules = out.replace(/@property [^{]+\{[^}]*\}/g, "").replace(/in (oklch|oklab)/g, "");
    if (/var\(--color-/.test(rules)) problems.push(`${c}: uses Tailwind's palette, not a token`);
    if (LITERAL.test(rules)) problems.push(`${c}: a literal colour`);
  });
  return problems;
}

describe("components/ui: shadcn's primitives take their colours from the tokens", () => {
  const files = componentFiles("components/ui");

  it("has primitives to check", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files)("%s", (file) => {
    expect(colourProblems(file)).toEqual([]);
  });
});

describe("our components (shell, data, ai, policy): tokens only, no arbitrary values (CLAUDE.md §5)", () => {
  const files = ["components/shell", "components/data", "components/ai", "components/policy"].flatMap(componentFiles);

  it("has components to check", () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files)("%s takes its colours from the tokens", (file) => {
    expect(colourProblems(file)).toEqual([]);
  });

  it.each(files)("%s uses no bracketed values, and only declared token variables", (file) => {
    const problems: string[] = [];
    for (const c of classesIn(readWeb(file))) {
      const utility = utilityOf(c);
      if (ds.candidatesToCss([c])[0] === null) continue; // not a Tailwind class
      if (/\[/.test(utility)) problems.push(`${c}: an arbitrary value`);
      for (const [, name] of utility.matchAll(/\((--[\w-]+)\)/g)) {
        if (!root.has(name)) problems.push(`${c}: ${name} isn't a token in globals.css`);
      }
    }
    expect(problems).toEqual([]);
  });

  it("catches what it should", () => {
    // the checks themselves: a palette colour, a literal and a bracketed size are refused; tokens pass
    const out = ds.candidatesToCss(["bg-red-500", "bg-[#123456]", "h-[48px]", "h-(--topbar-h)", "bg-panel"]);
    expect(out[0]).toContain("var(--color-red-500)");
    expect(LITERAL.test(out[1] ?? "")).toBe(true);
    expect(utilityOf("md:h-[48px]")).toBe("h-[48px]");
    expect(out[3]).toContain("var(--topbar-h)");
    expect(out[4]).toContain("var(--panel)");
  });
});

describe("fonts.ts (Design 01 §2.7)", () => {
  const fonts = readWeb("app/fonts.ts");

  it("loads IBM Plex Sans and Mono with the latin-ext subset (₹ lives there)", () => {
    expect(fonts).toMatch(/IBM_Plex_Sans\(\{\s*subsets: \["latin", "latin-ext"\],\s*weight: \["400", "500", "600"\],\s*variable: "--font-plex-sans",\s*display: "swap",\s*\}\)/);
    expect(fonts).toMatch(/IBM_Plex_Mono\(\{\s*subsets: \["latin", "latin-ext"\],\s*weight: \["400", "500"\],\s*variable: "--font-plex-mono",\s*display: "swap",\s*\}\)/);
  });

  it("is what the root layout puts on <html>", () => {
    const layout = readWeb("app/layout.tsx");
    expect(layout).toContain('import { plexMono, plexSans } from "./fonts"');
    expect(layout).toMatch(/className=\{`\$\{plexSans\.variable\} \$\{plexMono\.variable\} dark/);
    expect(layout).not.toMatch(/Geist/);
  });
});

describe("web/design/specimen.html (the visual reference)", () => {
  it("uses globals.css's values for every token they share", () => {
    const specimen = rootVariables(readWeb("design/specimen.html"));
    const shared = [...specimen.keys()].filter((k) => root.has(k));
    // Every colour token, less the two status aliases and the two non-colour values the specimen writes inline.
    const expected = Object.keys(DESIGN_TOKENS).filter((n) => !["--ok", "--error", "--band-alpha", "--market-edge"].includes(n));
    expect(shared.sort()).toEqual(expected.sort());
    for (const name of shared) expect(normalise(specimen.get(name)!), name).toBe(normalise(root.get(name)!));
  });
});
