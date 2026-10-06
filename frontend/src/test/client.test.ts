import { describe, expect, it, vi } from "vitest";

import { ApiError, api, errorMessage, onUnauthorized } from "../api/client";
import { jsonResponse, mockFetch } from "./utils";

describe("api client", () => {
  it("sends the CSRF cookie value on state-changing requests only", async () => {
    document.cookie = "fs_csrf=token-123; path=/";
    const fetchMock = mockFetch(() => jsonResponse(200, { ok: true }));
    await api.get("/funds");
    await api.post("/portfolios", { name: "x" });
    const getHeaders = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    const postHeaders = fetchMock.mock.calls[1][1]?.headers as Record<string, string>;
    expect(getHeaders["X-CSRF-Token"]).toBeUndefined();
    expect(postHeaders["X-CSRF-Token"]).toBe("token-123");
    expect(fetchMock.mock.calls[1][1]?.credentials).toBe("same-origin");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ name: "x" }));
  });

  it("turns the error envelope into an ApiError with details", async () => {
    mockFetch(() =>
      jsonResponse(422, {
        error: { code: "infeasible", message: "Raise the maximum weight.", details: { n_assets: 4 } },
      }),
    );
    const error = (await api.post("/portfolios/x/optimize", {}).catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.code).toBe("infeasible");
    expect(error.details).toEqual({ n_assets: 4 });
    expect(errorMessage(error)).toContain("Raise the maximum weight.");
  });

  it("includes field-level validation problems in the message", async () => {
    mockFetch(() =>
      jsonResponse(422, {
        error: { code: "validation_failed", message: "Request validation failed.", details: { problems: [{ location: ["body", "email"], message: "not a valid email" }] } },
      }),
    );
    const error = await api.post("/auth/register", {}).catch((e) => e);
    expect(errorMessage(error)).toContain("email: not a valid email");
  });

  it("notifies listeners on 401", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    mockFetch(() => jsonResponse(401, { error: { code: "not_authenticated", message: "Sign in to continue." } }));
    await expect(api.get("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await api.get("/funds").catch((e) => e)) as ApiError;
    expect(error.code).toBe("network_error");
  });

  it("returns undefined for 204 responses", async () => {
    mockFetch(() => new Response(null, { status: 204 }));
    await expect(api.delete("/portfolios/1")).resolves.toBeUndefined();
  });
});
