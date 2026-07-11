import { describe, expect, it } from "vitest";
import { isUrlContextReady } from "./useSessionBootstrap";


describe("session URL readiness", () => {
  it("requires the full normalized context before loading", () => {
    const tenant = { user_id: "user-a", namespace: "docs" };
    expect(isUrlContextReady(
      new URLSearchParams("session=s1&user_id=user-a&namespace=docs"),
      "s1",
      tenant
    )).toBe(true);
    expect(isUrlContextReady(
      new URLSearchParams("session=s1&user_id=user-a"),
      "s1",
      tenant
    )).toBe(false);
    expect(isUrlContextReady(
      new URLSearchParams("session=old&user_id=user-a&namespace=docs"),
      "s1",
      tenant
    )).toBe(false);
  });
});
