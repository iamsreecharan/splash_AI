import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisHistory } from "../src/AnalysisHistory";
import { setAccessToken } from "../src/api";
import type { Repository, Run } from "../src/types";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function run(id: string, title: string): Run {
  return {
    id,
    kind: "analyze",
    repository_id: "repo-a",
    repository_name: "team/payments",
    pr_number: 42,
    title,
    status: "completed",
    created_at: "2026-10-01T12:00:00Z",
    completed_at: "2026-10-01T12:01:00Z",
    alert_count: 2,
    error: null,
    stages: [],
  };
}

const repositories: Repository[] = [
  {
    id: "repo-a",
    full_name: "team/payments",
    description: "Payment services",
    default_branch: "main",
    language: "TypeScript",
    status: "ready",
    indexed_files: 10,
    indexed_chunks: 20,
    last_indexed_at: null,
    html_url: "https://github.com/team/payments",
    is_demo: false,
  },
  {
    id: "repo-b",
    full_name: "team/dashboard",
    description: "Web dashboard",
    default_branch: "main",
    language: "TypeScript",
    status: "ready",
    indexed_files: 6,
    indexed_chunks: 12,
    last_indexed_at: null,
    html_url: "https://github.com/team/dashboard",
    is_demo: false,
  },
];

function page(
  items: Run[],
  options: { offset?: number; total?: number; hasMore?: boolean } = {},
) {
  return new Response(
    JSON.stringify({
      items,
      total: options.total ?? items.length,
      limit: 20,
      offset: options.offset ?? 0,
      has_more: options.hasMore ?? false,
    }),
    { status: 200 },
  );
}

function mockHistory(handler: (url: URL) => Response | Promise<Response>) {
  const fetcher = vi.fn((input: RequestInfo | URL, _options?: RequestInit) =>
    Promise.resolve(handler(new URL(String(input), "http://localhost"))),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function lastQuery(fetcher: ReturnType<typeof mockHistory>) {
  const calls = fetcher.mock.calls;
  return new URL(String(calls[calls.length - 1][0]), "http://localhost")
    .searchParams;
}

beforeEach(() => {
  setAccessToken("workspace-a-token");
});

afterEach(() => {
  cleanup();
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("Analysis history", () => {
  it("uses authenticated server pagination and combines filters while resetting the page", async () => {
    const fetcher = mockHistory((url) => {
      const offset = Number(url.searchParams.get("offset") || 0);
      const filtered = Boolean(url.searchParams.get("q"));
      return page(
        [
          run(
            filtered ? "filtered" : `page-${offset}`,
            filtered
              ? "Filtered payment change"
              : offset
                ? "Older payment change"
                : "Latest payment change",
          ),
        ],
        { offset, total: 41, hasMore: offset < 40 },
      );
    });
    const onOpenRun = vi.fn();
    render(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={onOpenRun}
      />,
    );

    await screen.findByText("Latest payment change");
    const [url, options] = fetcher.mock.calls[0];
    expect(new URL(String(url), "http://localhost").pathname).toBe("/api/runs");
    expect(lastQuery(fetcher).get("limit")).toBe("20");
    expect(lastQuery(fetcher).get("offset")).toBe("0");
    expect(new Headers(options?.headers).get("Authorization")).toBe(
      "Bearer workspace-a-token",
    );
    expect(
      screen.getByRole("button", { name: "Previous page" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Older payment change");
    expect(lastQuery(fetcher).get("offset")).toBe("20");
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Latest payment change");
    expect(lastQuery(fetcher).get("offset")).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Older payment change");

    fireEvent.change(screen.getByLabelText("Search analysis history"), {
      target: { value: "payments & plans" },
    });
    await screen.findByText("Filtered payment change");
    expect(lastQuery(fetcher).get("q")).toBe("payments & plans");
    expect(lastQuery(fetcher).get("offset")).toBe("0");

    fireEvent.change(screen.getByLabelText("Filter history status"), {
      target: { value: "failed" },
    });
    await waitFor(() =>
      expect(lastQuery(fetcher).get("status")).toBe("failed"),
    );
    fireEvent.change(screen.getByLabelText("Filter history project"), {
      target: { value: "repo-b" },
    });
    await waitFor(() =>
      expect(lastQuery(fetcher).get("repository_id")).toBe("repo-b"),
    );
    expect(lastQuery(fetcher).get("q")).toBe("payments & plans");
    expect(lastQuery(fetcher).get("status")).toBe("failed");
    expect(lastQuery(fetcher).get("offset")).toBe("0");

    fireEvent.click(await screen.findByText("Filtered payment change"));
    expect(onOpenRun).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "filtered",
        title: "Filtered payment change",
      }),
    );
  });

  it("hides prior filter results immediately and ignores a late response from an older query", async () => {
    const olderQuery = deferred<Response>();
    const fetcher = mockHistory((url) => {
      const query = url.searchParams.get("q");
      if (query === "earlier") return olderQuery.promise;
      if (query === "latest")
        return page([run("new", "Current filtered result")]);
      return page([run("initial", "Initial history result")]);
    });
    render(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={vi.fn()}
      />,
    );
    await screen.findByText("Initial history result");

    fireEvent.change(screen.getByLabelText("Search analysis history"), {
      target: { value: "earlier" },
    });
    expect(
      screen.queryByText("Initial history result"),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(lastQuery(fetcher).get("q")).toBe("earlier"));
    fireEvent.change(screen.getByLabelText("Search analysis history"), {
      target: { value: "latest" },
    });
    await screen.findByText("Current filtered result");

    await act(async () => {
      olderQuery.resolve(page([run("stale", "Stale filtered result")]));
      await olderQuery.promise;
    });
    expect(screen.queryByText("Stale filtered result")).not.toBeInTheDocument();
    expect(screen.getByText("Current filtered result")).toBeInTheDocument();
  });

  it("clears old workspace rows, filters, and pagination before the new account responds", async () => {
    const nextWorkspace = deferred<Response>();
    let workspace = "a";
    const fetcher = mockHistory((url) => {
      if (workspace === "b") return nextWorkspace.promise;
      const offset = Number(url.searchParams.get("offset") || 0);
      return page(
        [
          run(
            `private-${offset}`,
            offset ? "Private second page" : "Private first page",
          ),
        ],
        {
          offset,
          total: 41,
          hasMore: true,
        },
      );
    });
    const onOpenRun = vi.fn();
    const { rerender } = render(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={onOpenRun}
      />,
    );
    await screen.findByText("Private first page");
    fireEvent.change(screen.getByLabelText("Search analysis history"), {
      target: { value: "private" },
    });
    await waitFor(() => expect(lastQuery(fetcher).get("q")).toBe("private"));
    fireEvent.change(screen.getByLabelText("Filter history status"), {
      target: { value: "completed" },
    });
    await waitFor(() =>
      expect(lastQuery(fetcher).get("status")).toBe("completed"),
    );
    fireEvent.change(screen.getByLabelText("Filter history project"), {
      target: { value: "repo-a" },
    });
    await waitFor(() =>
      expect(lastQuery(fetcher).get("repository_id")).toBe("repo-a"),
    );
    await screen.findByText("Private first page");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Private second page");

    workspace = "b";
    setAccessToken("workspace-b-token");
    rerender(
      <AnalysisHistory
        workspaceKey="workspace-b"
        repositories={[]}
        onOpenRun={onOpenRun}
      />,
    );
    expect(screen.queryByText("Private second page")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Search analysis history")).toHaveValue("");
    expect(screen.getByLabelText("Filter history status")).toHaveValue("");
    expect(screen.getByLabelText("Filter history project")).toHaveValue("");
    await waitFor(() => {
      expect(lastQuery(fetcher).get("offset")).toBe("0");
      expect(lastQuery(fetcher).get("q") || "").toBe("");
      expect(lastQuery(fetcher).get("status") || "").toBe("");
      expect(lastQuery(fetcher).get("repository_id") || "").toBe("");
    });
    const options = fetcher.mock.calls.at(-1)?.[1];
    expect(new Headers(options?.headers).get("Authorization")).toBe(
      "Bearer workspace-b-token",
    );

    await act(async () => {
      nextWorkspace.resolve(
        page([run("new-workspace", "New workspace result")]),
      );
      await nextWorkspace.promise;
    });
    await screen.findByText("New workspace result");
    expect(
      screen.getByRole("button", { name: "Previous page" }),
    ).toBeDisabled();
    expect(screen.queryByText("Private first page")).not.toBeInTheDocument();
    expect(screen.queryByText("Private second page")).not.toBeInTheDocument();
  });

  it("shows loading, actionable errors, retry, and a genuine empty state", async () => {
    const pending = deferred<Response>();
    let attempts = 0;
    mockHistory(() => (++attempts === 1 ? pending.promise : page([])));
    render(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading analysis history",
    );
    expect(screen.queryByText("No runs found.")).not.toBeInTheDocument();

    await act(async () => {
      pending.resolve(
        new Response(
          JSON.stringify({
            detail: "History temporarily unavailable. Try again.",
          }),
          {
            status: 503,
          },
        ),
      );
      await pending.promise;
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "History temporarily unavailable. Try again.",
    );
    expect(screen.queryByText("No runs found.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("No runs found.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Next page" }),
    ).not.toBeInTheDocument();
  });

  it("refreshes the current history query when a run changes", async () => {
    let refreshed = false;
    const fetcher = mockHistory(() =>
      page([
        run("same-run", refreshed ? "Completed analysis" : "Queued analysis"),
      ]),
    );
    const onOpenRun = vi.fn();
    const { rerender } = render(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={onOpenRun}
        refreshKey={1}
      />,
    );
    await screen.findByText("Queued analysis");
    fireEvent.change(screen.getByLabelText("Search analysis history"), {
      target: { value: "checkout" },
    });
    await waitFor(() => expect(lastQuery(fetcher).get("q")).toBe("checkout"));
    const previousCalls = fetcher.mock.calls.length;
    refreshed = true;
    rerender(
      <AnalysisHistory
        workspaceKey="workspace-a"
        repositories={repositories}
        onOpenRun={onOpenRun}
        refreshKey={2}
      />,
    );
    await screen.findByText("Completed analysis");
    expect(fetcher.mock.calls.length).toBeGreaterThan(previousCalls);
    expect(lastQuery(fetcher).get("q")).toBe("checkout");
  });
});
