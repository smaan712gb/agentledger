import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, expect } from "vitest";
import * as axeMatchers from "vitest-axe/matchers";

import { server } from "./msw/server";

expect.extend(axeMatchers);

beforeAll(() => {
  // Anything the fixtures do not answer is a mistake in the test, not a passthrough.
  server.listen({ onUnhandledFrame: "error" });
});

afterEach(() => {
  server.resetHandlers();
  cleanup();
  try {
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});

afterAll(() => {
  server.close();
});

// jsdom lacks a few browser APIs the components touch.
if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { value: ResizeObserverStub });
}
Element.prototype.scrollIntoView = () => {};
if (!("PointerEvent" in globalThis)) {
  Object.defineProperty(globalThis, "PointerEvent", { value: MouseEvent });
}
// The router's scroll restoration calls window.scrollTo, which jsdom only logs as "not implemented".
Object.defineProperty(window, "scrollTo", { value: () => {}, writable: true });
// The document viewer shows an inline image through an object URL; jsdom has no URL.createObjectURL.
if (typeof URL.createObjectURL !== "function") {
  Object.defineProperty(URL, "createObjectURL", { value: () => "blob:jsdom/test", writable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: () => {}, writable: true });
}
