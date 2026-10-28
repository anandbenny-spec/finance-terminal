// web/lib/policy/sections.ts: the Fed vs RBI page's sections, in page order (#147; Design 01 §1.11, §4.2). One list,
// read by /policy for its three `<Section id title>`s and by the menu (lib/shell/nav.ts) for its section links, so
// renaming a section or its id can't leave a dead link in the sidebar or the drawer.

export type PolicySection = { id: string; title: string };

export const POLICY_SECTIONS = {
  whereRatesStand: { id: "where-rates-stand", title: "Where rates stand" },
  latestDecisions: { id: "latest-decisions", title: "Latest decisions" },
  history: { id: "history", title: "History" },
} as const satisfies Record<string, PolicySection>;

/** The sections in the order the page shows them, which is the order the menu lists them. */
export const POLICY_SECTION_LIST: readonly PolicySection[] = [
  POLICY_SECTIONS.whereRatesStand,
  POLICY_SECTIONS.latestDecisions,
  POLICY_SECTIONS.history,
];

/** A section's link: /policy#history. The heading's `scroll-mt-16` puts it just under the top bar. */
export function policySectionHref(section: PolicySection): string {
  return `/policy#${section.id}`;
}
