// A server component must not call a function exported from a "use client" file: Next throws "Attempted to call X() from the
// server but X is on the client" on the first render that reaches the call (#475: CallsTab called callHref, which only ran once
// equity.remarks_change was released). jsdom has no client boundary, so the component tests can't see it; this reads the files.
// Components imported and passed as JSX are fine; only a call of a lower-case named import counts.
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = resolve(__dirname, "..");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

export function isClientFile(src: string): boolean {
  const body = src.replace(/^(\s|\/\/[^\n]*\n|\/\*[\s\S]*?\*\/)*/, "");
  return /^["']use client["']/.test(body);
}

function resolveImport(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(WEB, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(from), spec);
  else return null;
  for (const c of [`${base}.tsx`, `${base}.ts`, join(base, "index.tsx"), join(base, "index.ts")]) if (existsSync(c)) return c;
  return null;
}

/** Names a server file imports (value imports, lower-case first letter) from a client file, and which of them it calls. */
export function offenders(file: string, src: string, isClient: (path: string) => boolean): string[] {
  const found: string[] = [];
  const re = /import\s+(?!type\b)\{([^}]*)\}\s+from\s+["']([^"']+)["']/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    const target = resolveImport(file, m[2]);
    if (!target || !isClient(target)) continue;
    const rest = src.slice(0, m.index) + src.slice(m.index + m[0].length);
    for (const part of m[1].split(",")) {
      const spec = part.trim();
      if (!spec || spec.startsWith("type ")) continue;
      const local = spec.split(/\s+as\s+/).pop()!.trim();
      if (/^[a-z_]/.test(local) && new RegExp(`(^|[^\\w.])${local}\\s*\\(`).test(rest)) found.push(`${local} (from ${m[2]})`);
    }
  }
  return found;
}

const files = [...walk(join(WEB, "components")), ...walk(join(WEB, "app"))];
const cache = new Map<string, boolean>();
const isClient = (path: string) => {
  if (!cache.has(path)) cache.set(path, isClientFile(readFileSync(path, "utf8")));
  return cache.get(path)!;
};

describe("server components call no function from a client module", () => {
  it("finds the pattern that broke CallsTab (negative control)", () => {
    const src = `import { callHref, CallsTable } from "@/components/company/calls/CallTables";\nconst x = callHref(a, b);\n<CallsTable />`;
    expect(offenders(join(WEB, "components/company/calls/CallsTab.tsx"), src, () => true)).toEqual([
      "callHref (from @/components/company/calls/CallTables)",
    ]);
  });
  it("lets a client component be used as JSX and a client file call its own imports", () => {
    const src = `import { CallsTable } from "@/components/company/calls/CallTables";\n<CallsTable />`;
    expect(offenders(join(WEB, "components/company/calls/CallsTab.tsx"), src, () => true)).toEqual([]);
  });
  it("no server file under web/components or web/app calls a client module's function", () => {
    const bad: string[] = [];
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      if (isClientFile(src)) continue;
      for (const o of offenders(f, src, isClient)) bad.push(`${f.slice(WEB.length + 1)} calls ${o}`);
    }
    expect(bad, "A server component calls a function exported from a \"use client\" file; move it to a plain module").toEqual([]);
  });
});
