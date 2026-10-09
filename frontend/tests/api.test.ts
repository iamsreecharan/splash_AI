import { afterEach, describe, expect, it, vi } from "vitest";
import { api, safeExternalUrl, setAccessToken } from "../src/api";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("sends an authenticated JSON mutation with same-origin cookies", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "resolved" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    setAccessToken("test-bearer");
    await api("/alerts/123/feedback", {
      method: "POST",
      body: JSON.stringify({ kind: "resolved" }),
    });
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("/api/alerts/123/feedback");
    expect(options.credentials).toBe("include");
    expect(options.headers.get("Authorization")).toBe("Bearer test-bearer");
    expect(options.headers.get("Content-Type")).toBe("application/json");
  });
  it("exposes actionable backend errors and survives non-JSON proxy errors", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ detail: "Configure your GitHub App first." }),
          { status: 503 },
        ),
      )
      .mockResolvedValueOnce(
        new Response("Proxy unavailable", { status: 502 }),
      );
    vi.stubGlobal("fetch", fetcher);
    await expect(api("/github/install")).rejects.toThrow(
      "Configure your GitHub App first.",
    );
    await expect(api("/session")).rejects.toThrow("Request failed (502).");
  });
  it("rejects executable and malformed external source links", () => {
    expect(safeExternalUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeExternalUrl("data:text/html,<script>")).toBeUndefined();
    expect(safeExternalUrl("/relative")).toBeUndefined();
    expect(
      safeExternalUrl("https://github.com/team/repo/blob/sha/file.py#L3"),
    ).toBe("https://github.com/team/repo/blob/sha/file.py#L3");
  });
});
