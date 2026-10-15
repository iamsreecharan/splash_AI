import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { setAccessToken } from "../src/api";
import type { Run } from "../src/types";

const auth = vi.hoisted(() => ({
  callback: null as null | ((event: string, session: unknown) => void),
}));

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { access_token: "account-a", user: { id: "a" } } },
        error: null,
      }),
      onAuthStateChange: (callback: typeof auth.callback) => {
        auth.callback = callback;
        return {
          data: {
            subscription: {
              unsubscribe: () => {
                auth.callback = null;
              },
            },
          },
        };
      },
    },
  }),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const response = (data: unknown) =>
  new Response(JSON.stringify(data), { status: 200 });

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: "run-123",
    kind: "analyze",
    repository_id: "repo-a",
    repository_name: "team/payments",
    pr_number: 42,
    title: "Payment consumer contract changed",
    status: "completed",
    created_at: "2026-10-01T12:00:00Z",
    completed_at: "2026-10-01T12:01:00Z",
    alert_count: 2,
    error: null,
    stages: [
      {
        name: "source_analysis",
        status: "completed",
        summary: "Compared changed signatures with verified callers.",
        duration_ms: 20,
      },
    ],
    ...overrides,
  };
}

function mockWorkspace(options: {
  summary: Run;
  onDetail?: () => Response | Promise<Response>;
  onReport?: () => Response | Promise<Response>;
}) {
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const account =
        new Headers(init?.headers).get("Authorization") === "Bearer account-b"
          ? "b"
          : "a";
      const items = account === "a" ? [options.summary] : [];
      switch (url.pathname) {
        case "/api/public/config":
          return response({
            mode: "pilot",
            supabase: {
              configured: true,
              url: "https://auth.test.example",
              anon_key: "public-test-key",
            },
          });
        case "/api/session":
          return response({
            user: {
              id: account,
              name: `Account ${account}`,
              email: `${account}@test.example`,
            },
            organization: { id: account, name: `Workspace ${account}` },
            mode: "pilot",
            capabilities: { demo: false, github: false, ai: false },
          });
        case "/api/overview":
          return response({
            repositories: 0,
            open_alerts: 0,
            high_severity_alerts: 0,
            analyzed_changes: items.length,
            recent_runs: items,
            recent_alerts: [],
          });
        case "/api/integrations":
          return response({
            github: { configured: false, connected: false, app_name: "" },
            openai: { configured: false, enabled: false, model: "" },
            supabase: { configured: true },
            mode: "pilot",
          });
        case "/api/repositories":
        case "/api/alerts":
          return response({ items: [] });
        case "/api/runs":
          return response({
            items,
            total: items.length,
            limit: 20,
            offset: 0,
            has_more: false,
          });
        case `/api/runs/${options.summary.id}`:
          return options.onDetail
            ? options.onDetail()
            : response(options.summary);
        case `/api/runs/${options.summary.id}/report`:
          return options.onReport
            ? options.onReport()
            : new Response("# Analysis report");
        default:
          throw new Error(`Unexpected test request: ${url.pathname}`);
      }
    },
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

beforeEach(() => {
  auth.callback = null;
});
afterEach(() => {
  cleanup();
  setAccessToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Run details", () => {
  it("fetches full run details with authentication and prevents a pending private report from downloading after account switch", async () => {
    const summary = run({ stages: [] });
    const detail = run({ title: "Server-verified payment analysis" });
    const report = deferred<Response>();
    const fetcher = mockWorkspace({
      summary,
      onDetail: () => response(detail),
      onReport: () => report.promise,
    });
    const createObjectURL = vi.fn(() => "blob:private-report");
    const NativeURL = URL;
    vi.stubGlobal(
      "URL",
      class extends NativeURL {
        static createObjectURL = createObjectURL;
        static revokeObjectURL = vi.fn();
      },
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: new RegExp(summary.title) }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Analysis details",
    });
    expect(
      within(dialog).getByRole("heading", { name: detail.title }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Compared changed signatures with verified callers.",
      ),
    ).toBeInTheDocument();
    const request = fetcher.mock.calls.find(
      ([input]) => String(input) === `/api/runs/${summary.id}`,
    );
    expect(request).toBeDefined();
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(
      "Bearer account-a",
    );

    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Download report",
        exact: true,
      }),
    );
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([input]) =>
          String(input).endsWith(`/runs/${summary.id}/report`),
        ),
      ).toBe(true),
    );
    await act(async () => {
      auth.callback?.("SIGNED_IN", {
        access_token: "account-b",
        user: { id: "b" },
      });
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await screen.findByText("Workspace b");
    await act(async () => {
      report.resolve(new Response("# Private workspace a report"));
      await report.promise;
    });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
    expect(document.querySelector("a[download]")).toBeNull();
    expect(screen.queryByText(detail.title)).not.toBeInTheDocument();
  });

  it("describes a completed indexing run without a synthetic PR or analysis conclusion", async () => {
    const indexing = run({
      kind: "index",
      pr_number: 0,
      alert_count: 0,
      title: "Index team/payments",
      stages: [
        {
          name: "index_repository",
          status: "completed",
          summary: "Indexed 18 source files.",
          duration_ms: 250,
        },
      ],
    });
    mockWorkspace({ summary: indexing });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: new RegExp(indexing.title) }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Analysis details",
    });
    expect(dialog).not.toHaveTextContent(/PR\s*#0/);
    expect(dialog).not.toHaveTextContent(/impacts? found/i);
    expect(
      within(dialog).queryByRole("button", { name: "Download report" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByText("Indexed 18 source files."),
    ).toBeInTheDocument();
  });

  it("keeps a queued analysis pending instead of claiming zero impacts or offering a report", async () => {
    const queued = run({
      status: "queued",
      completed_at: null,
      alert_count: 0,
      stages: [],
      title: "Queued payment analysis",
    });
    mockWorkspace({ summary: queued });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: new RegExp(queued.title) }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Analysis details",
    });
    expect(dialog).not.toHaveTextContent(/impacts? found/i);
    expect(
      within(dialog).queryByRole("button", { name: "Download report" }),
    ).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent(/waiting|queued|pending/i);
    expect(dialog).toHaveTextContent(/stage|worker/i);
  });

  it("does not reopen a slow run detail after navigating to Analysis History", async () => {
    const summary = run();
    const slowDetail = deferred<Response>();
    const fetcher = mockWorkspace({
      summary,
      onDetail: () => slowDetail.promise,
    });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: new RegExp(summary.title) }),
    );
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([input]) => String(input) === `/api/runs/${summary.id}`,
        ),
      ).toBe(true),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Analysis History", exact: true }),
    );
    await screen.findByRole("heading", {
      name: "Analysis history",
      exact: true,
    });
    await act(async () => {
      slowDetail.resolve(response(run({ title: "Late private run details" })));
      await slowDetail.promise;
    });
    expect(
      screen.queryByRole("dialog", { name: "Analysis details" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Late private run details"),
    ).not.toBeInTheDocument();
  });
});
