import { describe, expect, it } from "vitest";
import { cn } from "./utils";
import { authErrorMessage } from "./error-messages";

describe("cn", () => {
  it("merges Tailwind classes without duplicates", () => {
    expect(cn("px-2", "px-4", "text-sm")).toBe("px-4 text-sm");
  });

  describe("authErrorMessage", () => {
    it("maps backend error codes to Turkish copy", () => {
      expect(authErrorMessage("SLOT_TAKEN")).toBe(
        "Seçilen saat başka biri tarafından alınmış.",
      );
    });

    it("does not expose unknown backend messages", () => {
      expect(authErrorMessage("UNKNOWN")).toBe("İstek tamamlanamadı.");
    });
  });
});
