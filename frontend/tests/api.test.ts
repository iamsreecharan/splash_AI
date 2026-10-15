import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiText, safeExternalUrl, setAccessToken } from "../src/api";

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

  it("downloads authenticated report text without parsing it as JSON", async () => {
    const markdown = "# Pull request impact\n\nEvidence: src/billing.ts:12\n";
    const fetcher = vi.fn().mockResolvedValue(
      new Response(markdown, {
        status: 200,
        headers: { "Content-Type": "text/markdown; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    setAccessToken("report-token");
    const controller = new AbortController();

    await expect(
      apiText("/runs/run-123/report?format=markdown", {
        signal: controller.signal,
        headers: { Accept: "text/markdown", "X-Request-ID": "report-test" },
      }),
    ).resolves.toBe(markdown);

    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("/api/runs/run-123/report?format=markdown");
    expect(options.credentials).toBe("include");
    expect(options.signal).toBe(controller.signal);
    expect(options.headers.get("Authorization")).toBe("Bearer report-token");
    expect(options.headers.get("Accept")).toBe("text/markdown");
    expect(options.headers.get("X-Request-ID")).toBe("report-test");
  });

  it("preserves report errors from the backend and non-JSON gateways", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ detail: "Analysis run not found." }), {
            status: 404,
          }),
        )
        .mockResolvedValueOnce(
          new Response("Gateway unavailable", { status: 502 }),
        ),
    );

    await expect(apiText("/runs/missing/report")).rejects.toThrow(
      "Analysis run not found.",
    );
    await expect(apiText("/runs/123/report")).rejects.toThrow(
      "Request failed (502).",
    );
  });

  it("preserves cancellation when a report request is aborted", async () => {
    const controller = new AbortController();
    const cancelled = new DOMException(
      "The operation was aborted.",
      "AbortError",
    );
    controller.abort();
    const fetcher = vi.fn().mockRejectedValue(cancelled);
    vi.stubGlobal("fetch", fetcher);

    await expect(
      apiText("/runs/123/report", { signal: controller.signal }),
    ).rejects.toBe(cancelled);
    expect(fetcher.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
