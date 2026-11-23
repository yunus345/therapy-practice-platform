import { getRequestConfig } from "next-intl/server";

export default getRequestConfig(async () => ({
  locale: "tr",
  messages: (await import("../../messages/tr.json")).default,
}));
