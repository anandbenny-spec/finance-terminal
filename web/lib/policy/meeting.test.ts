// The meeting page's rows and copy (C4; Spec 01 §4.5): §4.5's sentences word for word, ¶ numbers from the stored
// spans, the RBI's cross-check note kept with its row. Test rows, not real decisions.
import { describe, expect, it } from "vitest";
import type { DocumentRow, ForecastRow, PolicyMeetingRow, PolicyRateSettingDetailRow, PolicyVoteRow } from "@/lib/queries/api";
import { paragraphs } from "./text";
import {
  SPECIAL_MEETING_NOTE,
  latestByType,
  meetingTitle,
  minutesNote,
  projectionRows,
  rateRows,
  releaseLine,
  scheduledNote,
  slugFor,
  speakerAnchors,
  transcriptNote,
  voteRows,
} from "./meeting";

const FED: PolicyMeetingRow = {
  slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
  kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: "2026-10-08", has_press_conference: true,
  policy_rate_pct: 3.5, policy_rate_change_bps: -25, stance: null, stance_display_label: null, dissent_count: 1,
  attribution: "Source: Federal Reserve Board", fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5,
};
const RBI: PolicyMeetingRow = {
  ...FED, slug: "mpc-2026-10-07", bank: "RBI", decision_date: "2026-10-07", decision_at: "2026-10-07T04:30:00+00:00", meeting_no: 61,
  minutes_due_on: "2026-10-21", policy_rate_pct: 5.5, policy_rate_change_bps: 25, fed_target_lower_pct: null, fed_target_upper_pct: null,
};

describe("routes and titles", () => {
  it("builds the slug from a real calendar date only", () => {
    expect(slugFor("Fed", "2026-09-17")).toBe("fomc-2026-09-17");
    expect(slugFor("RBI", "2026-10-07")).toBe("mpc-2026-10-07");
    expect(slugFor("Fed", "2026-02-30")).toBeNull();
    expect(slugFor("Fed", "17-09-2026")).toBeNull();
  });

  it("titles and release lines as Spec 01 §4.3 writes them", () => {
    expect(meetingTitle(FED)).toBe("FOMC meeting · 17 Sep 2026");
    expect(meetingTitle(RBI)).toBe("MPC meeting no. 61 · 7 Oct 2026");
    expect(releaseLine(FED)).toBe("Statement 14:00 ET (23:30 IST)");
    expect(releaseLine(RBI)).toBe("Resolution 10:00 IST");
  });

  it("keeps each document type's latest version", () => {
    const doc = (id: number, doc_type: string, version_no: number) => ({ id, doc_type, version_no }) as DocumentRow;
    const m = latestByType([doc(1, "fomc_statement", 1), doc(2, "fomc_statement", 2), doc(3, "fomc_minutes", 1)]);
    expect([...m.values()].map((d) => d.id)).toEqual([2, 3]);
  });
});

describe("§4.5 state copy", () => {
  it("scheduled meeting", () => {
    expect(scheduledNote({ ...FED, status: "scheduled", decision_date: "2026-10-28", decision_at: "2026-10-28T18:00:00+00:00" })).toBe(
      "Decision due 28 Oct 2026, 14:00 ET (23:30 IST).",
    );
    expect(scheduledNote({ ...RBI, status: "scheduled", decision_date: "2026-12-04", decision_at: "2026-12-04T04:30:00+00:00" })).toBe(
      "Decision due 4 Dec 2026, 10:00 IST.",
    );
  });

  it("minutes not out yet, word for word, and late minutes", () => {
    expect(minutesNote(RBI, "2026-10-10")).toBe("Minutes are due on 21 Oct 2026, 14 days after the meeting (RBI Act, s.45ZL).");
    expect(minutesNote(FED, "2026-09-20")).toBe("Minutes are due on 8 Oct 2026, three weeks after the decision.");
    expect(minutesNote(FED, "2026-10-12")).toBe("The minutes were due on 8 Oct 2026 and aren't on the site yet.");
  });

  it("Fed transcript missing: posted soon, then not published after 30 days", () => {
    expect(transcriptNote(FED, "2026-09-20")).toBe("The Fed hasn't posted this transcript yet.");
    expect(transcriptNote(FED, "2026-10-20")).toBe("Not published by the Fed.");
  });

  it("RBI: no press conference at the four 2020 COVID meetings (Anand, 23 Oct 2026)", () => {
    expect(transcriptNote({ ...RBI, decision_date: "2020-05-22" }, "2026-10-24")).toMatch(/video address/);
    expect(transcriptNote(RBI, "2026-10-09")).toBe("The RBI hasn't posted this transcript yet.");
  });

  it("special meeting no. 39", () => {
    expect(SPECIAL_MEETING_NOTE).toBe(
      "Special meeting to prepare the report to the Government on missing the inflation target (RBI Act, s.45ZN). No policy decision; no minutes published.",
    );
  });
});

describe("tables", () => {
  const text = "The MPC met.\n\nThe policy repo rate is increased by 25 basis points to 5.50 per cent.\n\nProjections follow.";
  const paras = paragraphs(text);
  const set = (instrument: PolicyRateSettingDetailRow["instrument"], value_pct: number, change_bps: number, crosscheck: string | null = null): PolicyRateSettingDetailRow => ({
    meeting_slug: RBI.slug, bank: "RBI", decision_date: RBI.decision_date, instrument, value_pct, change_bps, crosscheck, document_id: 9, char_start: 20, char_end: 60,
  });

  it("lists the rates in order with their ¶ and cross-check note", () => {
    const rows = rateRows([set("msf", 5.75, 25), set("repo", 5.5, 25), set("sdf", 5.25, 25, "note")], paras, 9);
    expect(rows.map((r) => [r.label, r.value, r.change, r.para, r.note])).toEqual([
      ["Policy repo rate", "5.50%", "+25 bps", 2, null],
      ["Standing deposit facility (SDF)", "5.25%", "+25 bps", 2, "note"],
      ["Marginal standing facility (MSF)", "5.75%", "+25 bps", 2, null],
    ]);
    expect(rateRows([set("repo", 5.5, 25)], paras, 10)[0].para).toBeNull(); // another document: no ¶ claimed
  });

  it("writes projections as published, ranges as ranges, with the AI label when it is one", () => {
    const f = (over: Partial<ForecastRow>): ForecastRow => ({
      series_key: "rbi.proj_cpi", series_name: "x", vintage_date: RBI.decision_date, target_label: "2026-27", target_start: null, target_end: null,
      value: 4.1, value_low: null, value_high: null, label: null, document_id: 9, char_start: 90, char_end: 95, ...over,
    });
    const rows = projectionRows([f({}), f({ series_key: "rbi.proj_gdp", value: null, value_low: 6.5, value_high: 6.8, label: "AI-extracted", target_label: "Q3" })], paras, 9);
    expect(rows.map((r) => [r.variable, r.period, r.value, r.para, r.label])).toEqual([
      ["CPI inflation", "2026-27", "4.1%", 3, null],
      ["Real GDP growth", "Q3", "6.5–6.8%", 3, "AI-extracted"],
    ]);
  });

  it("puts dissenters last, each with their stated preference", () => {
    const v = (name: string, bps: number | null, over: Partial<PolicyVoteRow> = {}): PolicyVoteRow => ({
      meeting_slug: FED.slug, member_slug: name, member_name: name, voted_change_bps: bps, stance_vote: null, is_dissent: false, dissent_text: null, ...over,
    });
    expect(voteRows(FED, [v("Zed", -50, { is_dissent: true }), v("Abe", -25), v("Kay", null, { is_dissent: true })]).map((r) => [r.member, r.vote, r.dissent])).toEqual([
      ["Abe", "−25", null],
      ["Kay", "—", "not on the rate"],
      ["Zed", "−50", "preferred −50 bps"],
    ]);
    const stanceOnly = voteRows(RBI, [{ ...v("Var", 25, { is_dissent: true, stance_vote: "neutral" }), meeting_slug: RBI.slug }]);
    expect(stanceOnly[0]).toMatchObject({ stance: "Neutral", dissent: "stance: voted neutral" });
  });

  it("anchors each member's first statement in minutes", () => {
    const minutes = paragraphs("Intro.\n\nStatement by Dr. A\n\nMore by A.\n\nStatement by Prof. B");
    const chunks = [
      { char_start: 0, speaker: null },
      { char_start: minutes[1].start, speaker: "Dr. A" },
      { char_start: minutes[2].start, speaker: "Dr. A" },
      { char_start: minutes[3].start, speaker: "Prof. B" },
    ];
    expect(speakerAnchors(chunks, minutes)).toEqual([{ speaker: "Dr. A", para: 2 }, { speaker: "Prof. B", para: 4 }]);
  });
});
