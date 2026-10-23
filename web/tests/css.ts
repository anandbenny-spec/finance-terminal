// Test helpers for the token file (not a test file itself: Vitest runs only *.test.ts).
// - rootVariables(): the custom properties a stylesheet declares in its first `:root` block.
// - loadDesignSystem(): globals.css compiled by the installed Tailwind, as `next build` compiles it, so a test
//   can ask what CSS a class produces (null when Tailwind doesn't know it, e.g. a colour with no token).
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { __unstable__loadDesignSystem } from "tailwindcss";

export const WEB = fileURLToPath(new URL("..", import.meta.url));

export function readWeb(relative: string): string {
  return readFileSync(path.join(WEB, relative), "utf8");
}

/** Custom properties declared in the first `:root { … }` block, values with whitespace collapsed. */
export function rootVariables(css: string): Map<string, string> {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const start = text.search(/:root\s*\{/);
  if (start < 0) throw new Error("no :root block");
  const open = text.indexOf("{", start);
  const close = text.indexOf("}", open);
  const vars = new Map<string, string>();
  for (const decl of text.slice(open + 1, close).split(";")) {
    const m = /^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/.exec(decl);
    if (m) vars.set(m[1], m[2].replace(/\s+/g, " "));
  }
  return vars;
}

/** Normalises a CSS value for comparison: lower case, one space, a leading zero on decimals (`.12` → `0.12`). */
export function normalise(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").replace(/(^|[^\d])\.(\d)/g, "$10.$2").trim();
}

/** Resolves `var(--x)` references against `vars` (what getComputedStyle returns for a custom property). */
export function resolveVar(vars: Map<string, string>, name: string, depth = 0): string {
  const value = vars.get(name);
  if (value === undefined) return "";
  if (depth > 10) throw new Error(`var() cycle at ${name}`);
  return value.replace(/var\((--[\w-]+)\)/g, (_, ref: string) => resolveVar(vars, ref, depth + 1));
}

/** Resolves a CSS @import the way Tailwind's own loader does: relative paths, then a package's `style` export. */
async function loadStylesheet(id: string, base: string) {
  let file: string;
  if (id.startsWith(".")) {
    file = path.resolve(base, id);
  } else {
    const parts = id.split("/");
    const name = id.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
    const sub = id.slice(name.length + 1);
    const dir = path.join(WEB, "node_modules", name);
    const pkg = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
    let target = pkg.exports?.[sub ? `./${sub}` : "."];
    if (target && typeof target === "object") target = target.style ?? target.default;
    file = path.join(dir, target ?? pkg.style ?? sub);
  }
  return { path: file, base: path.dirname(file), content: readFileSync(file, "utf8") };
}

export async function loadDesignSystem() {
  return __unstable__loadDesignSystem(readWeb("app/globals.css"), {
    base: path.join(WEB, "app"),
    loadStylesheet,
  });
}
