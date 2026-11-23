import messages from "../../messages/tr.json";
import { errorCodeValues, type components } from "./api-schema";

export type ErrorCode = components["schemas"]["ErrorCode"];

// Typed as a full record, so a backend code without a Turkish message fails the build.
const ERROR_MESSAGES: Record<ErrorCode, string> = messages.Errors;
const KNOWN_CODES: ReadonlySet<string> = new Set(errorCodeValues);

export function isErrorCode(code: unknown): code is ErrorCode {
  return typeof code === "string" && KNOWN_CODES.has(code);
}

/** Turkish text for a backend error code. Never shows the backend's own message. */
export function errorMessage(code?: string): string {
  return isErrorCode(code)
    ? ERROR_MESSAGES[code]
    : messages.Common.requestFailed;
}
