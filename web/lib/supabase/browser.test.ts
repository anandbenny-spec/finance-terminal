// The shell's admin check is display only, and reads app_metadata (which users can't edit), never user_metadata
// (CLAUDE.md §4). The API's own check is A3's.
import { describe, expect, it } from "vitest";
import { AUTH_STORAGE_KEY, hasStoredSession, isAdminUser } from "./browser";

describe("isAdminUser", () => {
  it("requires app_metadata.role = 'admin'", () => {
    expect(isAdminUser({ app_metadata: { role: "admin" } })).toBe(true);
    expect(isAdminUser({ app_metadata: {} })).toBe(false);
    expect(isAdminUser(null)).toBe(false);
  });

  it("ignores user_metadata.role, which the user can edit", () => {
    const user = { app_metadata: {}, user_metadata: { role: "admin" } };
    expect(isAdminUser(user)).toBe(false);
  });
});

describe("hasStoredSession", () => {
  it("looks for the session under the client's own storage key", () => {
    expect(hasStoredSession({ getItem: (k) => (k === AUTH_STORAGE_KEY ? "{}" : null) })).toBe(true);
    expect(hasStoredSession({ getItem: () => null })).toBe(false);
    expect(hasStoredSession(undefined)).toBe(false);
  });

  it("is false where storage is blocked", () => {
    expect(
      hasStoredSession({
        getItem: () => {
          throw new Error("SecurityError");
        },
      }),
    ).toBe(false);
  });
});
