import { describe, expect, it } from "vitest";

import { isNewVersion } from "./useBuildCheck";

describe("isNewVersion", () => {
  it("announces a release only when both sides carry a real build id and they differ", () => {
    expect(isNewVersion("abc", "def")).toBe(true);
    expect(isNewVersion("abc", "abc")).toBe(false);
    expect(isNewVersion("dev", "abc")).toBe(false);
    expect(isNewVersion("abc", "dev")).toBe(false);
    expect(isNewVersion("abc", null)).toBe(false);
  });
});
