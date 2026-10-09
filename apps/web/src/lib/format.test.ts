import { describe, expect, it } from "vitest";

import { formatBytes, formatMoney, formatPercent, parseApiDate } from "./format";

describe("formatMoney", () => {
  it("formats decimal strings exactly, without parsing them as floats", () => {
    expect(formatMoney("1234.50")).toBe("$1,234.50");
    expect(formatMoney("0.10")).toBe("$0.10");
    expect(formatMoney("123456789012345678.99")).toBe("$123,456,789,012,345,678.99");
  });

  it("shows a dash for missing values and leaves non-decimal text alone", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("")).toBe("—");
    expect(formatMoney("n/a")).toBe("n/a");
  });
});

describe("dates", () => {
  it("parses ISO dates as calendar dates (no time-zone shift)", () => {
    const d = parseApiDate("2026-03-31");
    expect(d?.getFullYear()).toBe(2026);
    expect(d?.getMonth()).toBe(2);
    expect(d?.getDate()).toBe(31);
  });

  it("treats SQLite timestamps as UTC", () => {
    expect(parseApiDate("2026-03-31 12:00:00")?.toISOString()).toBe("2026-03-31T12:00:00.000Z");
    expect(parseApiDate("garbage")).toBeNull();
    expect(parseApiDate(null)).toBeNull();
  });
});

describe("small formatters", () => {
  it("percent and bytes", () => {
    expect(formatPercent(0.8)).toBe("80%");
    expect(formatPercent(null)).toBe("—");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 kB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
