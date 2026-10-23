// web/NOTICE carries Lightweight Charts' attribution notice (its Apache 2.0 licence; Design 01 §1.5, §6). The notice
// is tied to the installed version, so an upgrade has to re-check the upstream NOTICE file at the new tag.
import { describe, expect, it } from "vitest";
import { readWeb } from "./css";

const notice = readWeb("NOTICE");
const pkg = JSON.parse(readWeb("package.json"));

describe("web/NOTICE", () => {
  it("names the installed lightweight-charts version and the tag its notice came from", () => {
    const version: string = pkg.dependencies["lightweight-charts"];
    expect(notice).toContain(`(npm: lightweight-charts ${version})`);
    expect(notice).toContain(`NOTICE file at tag v${version}`);
  });

  it("reproduces TradingView's attribution notice exactly as published (its \"с\" is Cyrillic, U+0441)", () => {
    expect(notice).toContain("    TradingView Lightweight Charts™\n    Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/\n");
  });

  it("records the link to tradingview.com that the licence requires", () => {
    expect(notice).toMatch(/Charts: TradingView Lightweight Charts", linking to https:\/\/www\.tradingview\.com\//);
  });
});
