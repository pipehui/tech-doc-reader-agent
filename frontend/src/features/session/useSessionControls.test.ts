import { describe, expect, it } from "vitest";
import {
  sessionSwitchSearch,
  tenantSwitchContext
} from "./useSessionControls";


describe("session control URL model", () => {
  it("switches session while preserving unrelated query and removing prompt", () => {
    expect(sessionSwitchSearch(
      "prompt=hello&debug=1",
      "session-2",
      { user_id: "user-a", namespace: "docs" }
    )).toBe("debug=1&session=session-2&user_id=user-a&namespace=docs");
  });

  it("normalizes tenant drafts before building the next context", () => {
    expect(tenantSwitchContext("prompt=hello", "session-1", " ", "bad/name"))
      .toEqual({
        tenant: { user_id: "default", namespace: "tech_docs" },
        search: "session=session-1&user_id=default&namespace=tech_docs"
      });
  });
});
