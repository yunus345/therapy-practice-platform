import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("merges Tailwind classes without duplicates", () => {
    expect(cn("px-2", "px-4", "text-sm")).toBe("px-4 text-sm");
  });
});
