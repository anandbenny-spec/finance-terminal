import { describe, expect, it } from "vitest";
import { siteOrigin } from "./site";

describe("siteOrigin (#86: the preview image's absolute URL)", () => {
  it("uses the production domain on a production build", () => {
    expect(siteOrigin({ VERCEL_ENV: "production", VERCEL_PROJECT_PRODUCTION_URL: "example.vercel.app", VERCEL_URL: "x-abc.vercel.app" }).origin).toBe(
      "https://example.vercel.app",
    );
  });
  it("uses the deployment's own host on a preview", () => {
    expect(siteOrigin({ VERCEL_ENV: "preview", VERCEL_PROJECT_PRODUCTION_URL: "example.vercel.app", VERCEL_URL: "x-abc.vercel.app" }).origin).toBe(
      "https://x-abc.vercel.app",
    );
  });
  it("tolerates a scheme or trailing slash in the value", () => {
    expect(siteOrigin({ VERCEL_ENV: "production", VERCEL_PROJECT_PRODUCTION_URL: "https://example.vercel.app/" }).origin).toBe("https://example.vercel.app");
  });
  it("falls back to localhost when Vercel's variables are absent", () => {
    expect(siteOrigin({}).origin).toBe("http://localhost:3000");
    expect(siteOrigin({ PORT: "4000" }).origin).toBe("http://localhost:4000");
  });
});
