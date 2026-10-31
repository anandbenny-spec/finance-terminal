// The site icon (1 Nov 2026, #228): sizes, type, and the /favicon.ico rewrite.
import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";
import { contentType as appleType, size as appleSize } from "./apple-icon";
import { contentType, size } from "./icon";

describe("site icons", () => {
  it("icon is a 32 px PNG, apple-icon a 180 px PNG", () => {
    expect(size).toEqual({ width: 32, height: 32 });
    expect(contentType).toBe("image/png");
    expect(appleSize).toEqual({ width: 180, height: 180 });
    expect(appleType).toBe("image/png");
  });
  it("rewrites /favicon.ico to the icon route", async () => {
    const rules = await nextConfig.rewrites!();
    expect(rules).toEqual([{ source: "/favicon.ico", destination: "/icon" }]);
  });
});
