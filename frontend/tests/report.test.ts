import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setAccessToken } from "../src/api";
import { downloadRunReport } from "../src/report";

const createObjectURL = vi.fn((_blob: Blob) => "blob:splash-report-test");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  const NativeURL = URL;
  vi.stubGlobal(
    "URL",
    class extends NativeURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  setAccessToken("workspace-report-token");
});

afterEach(() => {
  setAccessToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Markdown report download", () => {
  it("downloads the authenticated report as Markdown and releases the temporary file", async () => {
    const markdown = "# Analysis report\n\nSource: src/payments.ts:12\n";
    const fetcher = vi.fn().mockResolvedValue(
      new Response(markdown, {
        headers: { "Content-Type": "text/markdown" },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    let downloaded:
      { href: string; name: string; attached: boolean } | undefined;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        downloaded = {
          href: this.href,
          name: this.download,
          attached: this.isConnected,
        };
      });

    await expect(downloadRunReport("run-123", () => true)).resolves.toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe("/api/runs/run-123/report");
    expect(
      new Headers(fetcher.mock.calls[0][1].headers).get("Authorization"),
    ).toBe("Bearer workspace-report-token");
    expect(click).toHaveBeenCalledOnce();
    expect(downloaded).toEqual({
      href: "blob:splash-report-test",
      name: "splash-analysis-run-123.md",
      attached: true,
    });
    const blob = createObjectURL.mock.calls[0][0];
    expect(blob.type).toBe("text/markdown;charset=utf-8");
    const contents = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsText(blob);
    });
    expect(contents).toBe(markdown);
    expect(document.querySelector("a[download]")).toBeNull();
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:splash-report-test"),
    );
  });

  it("does not download a late private report after the workspace changes", async () => {
    let resolve!: (response: Response) => void;
    const pending = new Promise<Response>((res) => {
      resolve = res;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending));
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    let workspaceIsCurrent = true;
    const download = downloadRunReport("private-run", () => workspaceIsCurrent);

    workspaceIsCurrent = false;
    resolve(new Response("# Private workspace report"));
    await expect(download).resolves.toBe(false);
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("releases a prepared file if the initiating session becomes stale before clicking", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("# Private report")),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const isCurrent = vi.fn().mockReturnValueOnce(true).mockReturnValue(false);

    await expect(downloadRunReport("private-run", isCurrent)).resolves.toBe(
      false,
    );
    expect(click).not.toHaveBeenCalled();
    expect(document.querySelector("a[download]")).toBeNull();
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:splash-report-test"),
    );
  });

  it("surfaces report authorization errors without creating or downloading a file", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: "Analysis run not found." }), {
            status: 404,
          }),
        ),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    await expect(
      downloadRunReport("other-account-run", () => true),
    ).rejects.toThrow("Analysis run not found.");
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
  });
});
