import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../src/App";

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
const emptyIntegrations = {
  github: { configured: false, connected: false, app_name: "" },
  openai: { configured: false, enabled: false, model: "" },
  supabase: { configured: true },
  mode: "pilot",
};
const session = (id: string) => ({
  user: { id, name: `Account ${id}`, email: `${id}@test.example` },
  organization: { id, name: `Workspace ${id}` },
  mode: "pilot",
  capabilities: { demo: false, github: false, ai: false },
});
const overview = (count: number) => ({
  repositories: count,
  open_alerts: 0,
  high_severity_alerts: 0,
  analyzed_changes: 0,
  recent_runs: [],
  recent_alerts: [],
});

beforeEach(() => {
  auth.callback = null;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("does not let a previous account response overwrite the new workspace", async () => {
  const oldOverview = deferred<Response>();
  let oldRequested = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      const token = new Headers(options.headers).get("Authorization");
      if (url.endsWith("/public/config"))
        return response({
          mode: "pilot",
          supabase: {
            configured: true,
            url: "https://auth.test.example",
            anon_key: "public-test-key",
          },
        });
      if (url.endsWith("/session"))
        return response(session(token === "Bearer account-b" ? "b" : "a"));
      if (url.endsWith("/overview")) {
        if (token === "Bearer account-a") {
          oldRequested = true;
          return oldOverview.promise;
        }
        return response(overview(7));
      }
      if (url.endsWith("/integrations")) return response(emptyIntegrations);
      return response({ items: [] });
    }),
  );
  render(<App />);
  await waitFor(() => expect(oldRequested).toBe(true));
  await act(async () => {
    auth.callback?.("SIGNED_IN", {
      access_token: "account-b",
      user: { id: "b" },
    });
  });
  await screen.findByText("Workspace b");
  await waitFor(() => expect(screen.getByText("7")).toBeInTheDocument());
  await act(async () => {
    oldOverview.resolve(response(overview(999)));
    await oldOverview.promise;
  });
  expect(screen.queryByText("999")).not.toBeInTheDocument();
  expect(screen.getByText("7")).toBeInTheDocument();
});

it("clears private workspace content immediately on sign out", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/public/config"))
        return response({
          mode: "pilot",
          supabase: {
            configured: true,
            url: "https://auth.test.example",
            anon_key: "public-test-key",
          },
        });
      if (url.endsWith("/session")) return response(session("a"));
      if (url.endsWith("/overview")) return response(overview(42));
      if (url.endsWith("/integrations")) return response(emptyIntegrations);
      return response({ items: [] });
    }),
  );
  render(<App />);
  await screen.findByText("42");
  await act(async () => {
    auth.callback?.("SIGNED_OUT", null);
  });
  expect(screen.queryByText("42")).not.toBeInTheDocument();
  expect(screen.queryByText("Workspace a")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
});
