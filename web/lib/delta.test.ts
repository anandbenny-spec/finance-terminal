// Delta's change (Design 01 §4.3): direction follows the sign the reader sees, and a screen reader hears words.
import { describe, expect, it } from "vitest";
import { changeOf } from "./delta";
import { formatPctChange, formatYieldChange } from "./format";

describe("changeOf", () => {
  it("reads the direction from format.ts's sign", () => {
    expect(changeOf(formatPctChange(0.4213))).toEqual({ text: "+0.42%", direction: "up", spoken: "up 0.42 percent" });
    expect(changeOf(formatPctChange(-0.4213))).toEqual({ text: "−0.42%", direction: "down", spoken: "down 0.42 percent" });
    expect(changeOf(formatYieldChange(-0.03))).toEqual({ text: "−3 bps", direction: "down", spoken: "down 3 basis points" });
  });

  it("calls a move that rounds to zero flat, so a coloured 0.00% never shows", () => {
    expect(changeOf(formatPctChange(0.001))).toEqual({ text: "0.00%", direction: "flat", spoken: "unchanged, 0.00 percent" });
    expect(changeOf(formatYieldChange(0.001))).toEqual({ text: "0 bps", direction: "flat", spoken: "unchanged, 0 basis points" });
  });
});
