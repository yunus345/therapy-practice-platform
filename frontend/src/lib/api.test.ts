import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./api";

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves an API error code instead of exposing the backend message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "SLOT_TAKEN", message: "private backend message" },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(apiFetch("/appointments")).rejects.toMatchObject({
      status: 409,
      code: "SLOT_TAKEN",
      message: "SLOT_TAKEN",
    } satisfies Partial<ApiError>);
  });
});
