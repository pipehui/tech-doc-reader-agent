import { describe, expect, it, vi } from "vitest";
import {
  ApiContractError,
  createJsonClient,
  HttpError
} from "./client";


describe("shared JSON client", () => {
  it("adds tenant identity, forwards AbortSignal and decodes JSON", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn(async () => new Response(
      JSON.stringify({ ok: true }),
      { status: 200, headers: { "content-type": "application/json" } }
    ));
    const client = createJsonClient({ baseUrl: "/api", fetchImpl });

    await expect(client.request("/sessions/s1?include=true", {
      tenant: { user_id: "user-a", namespace: "docs:zh" },
      signal: controller.signal,
      decode: (payload) => payload
    })).resolves.toEqual({ ok: true });

    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/sessions/s1?include=true&user_id=user-a&namespace=docs%3Azh",
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

  it("maps FastAPI detail and preserves structured error payload", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      detail: "Application resources are not initialized."
    }), { status: 503, statusText: "Service Unavailable" }));
    const client = createJsonClient({ fetchImpl });

    const error = await client.request("/learning/overview", {
      decode: (payload) => payload
    }).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({
      status: 503,
      message: "503 Application resources are not initialized.",
      payload: { detail: "Application resources are not initialized." }
    });
  });

  it("joins FastAPI validation messages and uses text fallback", async () => {
    const validationClient = createJsonClient({
      fetchImpl: vi.fn(async () => new Response(JSON.stringify({
        detail: [{ msg: "Field required" }, { msg: "Invalid tenant" }]
      }), { status: 422 }))
    });
    await expect(validationClient.request("/sessions", {
      decode: (payload) => payload
    })).rejects.toThrow("422 Field required; Invalid tenant");

    const textClient = createJsonClient({
      fetchImpl: vi.fn(async () => new Response("gateway offline", { status: 502 }))
    });
    await expect(textClient.request("/sessions", {
      decode: (payload) => payload
    })).rejects.toThrow("502 gateway offline");

    const htmlClient = createJsonClient({
      fetchImpl: vi.fn(async () => new Response(
        "<!doctype html><html><body>proxy failure</body></html>",
        { status: 500, statusText: "Internal Server Error" }
      ))
    });
    await expect(htmlClient.request("/sessions", {
      decode: (payload) => payload
    })).rejects.toThrow("500 Internal Server Error");
  });

  it("wraps invalid JSON and decoder failures as contract errors", async () => {
    const invalidJson = createJsonClient({
      fetchImpl: vi.fn(async () => new Response("not-json", { status: 200 }))
    });
    await expect(invalidJson.request("/sessions/s1/state", {
      decode: (payload) => payload
    })).rejects.toBeInstanceOf(ApiContractError);

    const invalidShape = createJsonClient({
      fetchImpl: vi.fn(async () => new Response("{}", { status: 200 }))
    });
    await expect(invalidShape.request("/sessions/s1/state", {
      decode: () => { throw new Error("session_id must be a string"); }
    })).rejects.toThrow(
      "API contract violation for /sessions/s1/state: session_id must be a string"
    );
  });
});
