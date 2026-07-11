import { describe, expect, it } from "vitest";
import { experiencePath, routeName } from "./routing";


describe("application routing", () => {
  it("maps supported top-level paths and falls back to landing", () => {
    expect(routeName("/studio/thread")).toBe("studio");
    expect(routeName("inspector")).toBe("inspector");
    expect(routeName("/learner")).toBe("learner");
    expect(routeName("/unknown")).toBe("landing");
    expect(routeName("/")).toBe("landing");
  });

  it("builds an encoded tenant-scoped experience URL", () => {
    expect(experiencePath(
      "studio",
      { user_id: "user-a", namespace: "docs:zh" },
      "session/1",
      "explain a & b"
    )).toBe(
      "/studio?session=session%2F1&user_id=user-a&namespace=docs%3Azh&prompt=explain+a+%26+b"
    );
  });
});
