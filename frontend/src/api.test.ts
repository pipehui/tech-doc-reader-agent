import { afterEach, describe, expect, it, vi } from "vitest";
import { getSessionState } from "./api";


afterEach(() => {
  vi.unstubAllGlobals();
});


describe("JSON API client", () => {
  it("forwards tenant identity and AbortSignal to fetch", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      session_id: "session/1"
    }), {
      status: 200,
      headers: { "content-type": "application/json" }
    }));
    vi.stubGlobal("fetch", fetchMock);

    await getSessionState(
      "session/1",
      { user_id: "user-a", namespace: "docs:zh" },
      { signal: controller.signal }
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/sessions/session%2F1/state?user_id=user-a&namespace=docs%3Azh",
      {
        headers: {
          Accept: "application/json",
          "x-user-id": "user-a",
          "x-namespace": "docs:zh"
        },
        signal: controller.signal
      }
    );
  });

  it("maps non-success responses to a stable status error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("missing", {
      status: 404,
      statusText: "Not Found"
    })));

    await expect(getSessionState("missing")).rejects.toThrow("404 Not Found");
  });
});
