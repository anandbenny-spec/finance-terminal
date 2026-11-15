// web/lib/policy/sections.ts: the Fed vs RBI page's sections, in page order (#147; Design 01 §1.11, §4.2). One list,
// read by /policy for its three `<Section id title>`s and by the menu (lib/shell/nav.ts) for its section links, so
// renaming a section or its id can't leave a dead link in the sidebar or the drawer.
//
// All six sections are in the menu since 15 Nov 2026 (#575; Anand, 17:18 IST): the statement-changes strip, votes over
// time and the language timeline joined the first three. The last two render only while their field policies are shown
// or labelled (both flipped on production, 13 and 14 Nov 2026); if one is unflipped, its menu link lands on the top of
// /policy until it is taken out here. `menu` is a shorter label for the sidebar where the heading is a question.

export type PolicySection = { id: string; title: string; menu?: string };

export const POLICY_SECTIONS = {
  whereRatesStand: { id: "where-rates-stand", title: "Where rates stand" },
  latestDecisions: { id: "latest-decisions", title: "Latest decisions" },
  statementChanges: { id: "statement-changes", title: "How much each statement changed", menu: "Statement changes" },
  votesOverTime: { id: "votes-over-time", title: "How members voted over time", menu: "Votes over time" },
  languageTimeline: { id: "language-timeline", title: "How the wording moved", menu: "Wording over time" },
  history: { id: "history", title: "History" },
} as const satisfies Record<string, PolicySection>;

/** The sections in the order the page shows them, which is the order the menu lists them. */
export const POLICY_SECTION_LIST: readonly PolicySection[] = [
  POLICY_SECTIONS.whereRatesStand,
  POLICY_SECTIONS.latestDecisions,
  POLICY_SECTIONS.statementChanges,
  POLICY_SECTIONS.votesOverTime,
  POLICY_SECTIONS.languageTimeline,
  POLICY_SECTIONS.history,
];

/** A section's label in the menu: its short `menu` label, or its heading. */
export function policySectionLabel(section: PolicySection): string {
  return section.menu ?? section.title;
}

/** A section's link: /policy#history. The heading's `scroll-mt-16` puts it just under the top bar. */
export function policySectionHref(section: PolicySection): string {
  return `/policy#${section.id}`;
}
