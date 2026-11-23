import { describe, expect, it } from "vitest";
import messages from "../../messages/tr.json";
import { errorCodeValues } from "./api-schema";
import { errorMessage, isErrorCode } from "./error-messages";

// The error-code contract with the backend: errorCodeValues is generated from its
// OpenAPI schema, so these tests fail when the two sides drift apart.
describe("error-code contract", () => {
  const mapped = Object.keys(messages.Errors).sort();
  const backend: string[] = [...errorCodeValues].sort();

  it("maps no code the backend does not have", () => {
    expect(mapped.filter((code) => !backend.includes(code))).toEqual([]);
  });

  it("has a Turkish message for every code the backend can return", () => {
    expect(backend.filter((code) => !mapped.includes(code))).toEqual([]);
  });

  it("has no empty message", () => {
    for (const text of Object.values(messages.Errors)) {
      expect(text.trim()).not.toBe("");
    }
  });
});

describe("errorMessage", () => {
  it("maps backend error codes to Turkish copy", () => {
    expect(errorMessage("SLOT_TAKEN")).toBe(
      "Seçilen saat başka biri tarafından alınmış.",
    );
  });

  it("does not expose unknown or missing codes", () => {
    expect(isErrorCode("UNKNOWN")).toBe(false);
    expect(errorMessage("UNKNOWN")).toBe(messages.Common.requestFailed);
    expect(errorMessage()).toBe(messages.Common.requestFailed);
  });
});
