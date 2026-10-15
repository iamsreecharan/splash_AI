import {
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileSearch,
  FolderGit2,
  GitPullRequest,
  LoaderCircle,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, friendlyError } from "./api";
import type { Repository, Run, RunPage } from "./types";

const PAGE_SIZE = 20;
type Controls = {
  workspaceKey: string;
  query: string;
  status: string;
  repositoryId: string;
  offset: number;
};
const defaultControls = (workspaceKey: string): Controls => ({
  workspaceKey,
  query: "",
  status: "",
  repositoryId: "",
  offset: 0,
});
function runDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function AnalysisHistory({
  workspaceKey,
  repositories,
  onOpenRun,
  refreshKey = 0,
}: {
  workspaceKey: string;
  repositories: Repository[];
  onOpenRun: (run: Run) => void;
  refreshKey?: number;
}) {
  const [controls, setControls] = useState(() => defaultControls(workspaceKey));
  // A new account uses neutral filters on its very first render.
  const current =
    controls.workspaceKey === workspaceKey
      ? controls
      : defaultControls(workspaceKey);
  const { query, status, repositoryId, offset } = current;
  const requestKey = JSON.stringify([
    workspaceKey,
    query.trim(),
    status,
    repositoryId,
    offset,
  ]);
  const [snapshot, setSnapshot] = useState<{
    key: string;
    page: RunPage;
  } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(
    null,
  );
  const [fetching, setFetching] = useState(false);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const currentKey = useRef(requestKey);
  currentKey.current = requestKey;

  useEffect(() => {
    setControls(defaultControls(workspaceKey));
  }, [workspaceKey]);

  const updateControls = (change: Partial<Omit<Controls, "workspaceKey">>) => {
    setControls((previous) => ({
      ...(previous.workspaceKey === workspaceKey
        ? previous
        : defaultControls(workspaceKey)),
      offset: 0,
      ...change,
    }));
  };

  const load = useCallback(async () => {
    const key = requestKey;
    const requestGeneration = ++generation.current;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setFetching(true);
    setError(null);
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (query.trim()) params.set("q", query.trim());
    if (status) params.set("status", status);
    if (repositoryId) params.set("repository_id", repositoryId);
    try {
      const result = await api<RunPage>(`/runs?${params}`, {
        signal: controller.signal,
      });
      if (
        controller.signal.aborted ||
        generation.current !== requestGeneration ||
        currentKey.current !== key
      )
        return;
      // A deletion may remove the final page while history is open.
      if (!result.items.length && result.total > 0 && offset >= result.total) {
        setControls((previous) => ({
          ...previous,
          offset: Math.floor((result.total - 1) / PAGE_SIZE) * PAGE_SIZE,
        }));
        return;
      }
      setSnapshot({ key, page: result });
    } catch (cause) {
      if (
        controller.signal.aborted ||
        generation.current !== requestGeneration ||
        currentKey.current !== key
      )
        return;
      setSnapshot(null);
      setError({ key, message: friendlyError(cause) });
    } finally {
      if (
        generation.current === requestGeneration &&
        currentKey.current === key
      )
        setFetching(false);
    }
  }, [requestKey, offset, query, status, repositoryId]);

  useEffect(() => {
    // Cancel obsolete requests immediately; wait briefly before authorizing a
    // new server query so typing does not repeatedly recheck GitHub access.
    const pending = setTimeout(() => void load(), 250);
    return () => {
      clearTimeout(pending);
      generation.current += 1;
      abort.current?.abort();
    };
  }, [load, refreshKey]);

  const result = snapshot?.key === requestKey ? snapshot.page : null;
  const activeError = error?.key === requestKey ? error.message : null;
  const hasActiveRun = result?.items.some(
    (run) => run.status === "queued" || run.status === "running",
  );
  useEffect(() => {
    const interval = setInterval(
      () => void load(),
      hasActiveRun ? 1500 : 12000,
    );
    return () => clearInterval(interval);
  }, [load, hasActiveRun]);

  const hasFilters = !!(query || status || repositoryId);
  return (
    <section className="history-page" aria-label="Analysis history">
      <div className="section-toolbar history-toolbar">
        <label className="search-field">
          <Search size={17} aria-hidden="true" />
          <input
            aria-label="Search analysis history"
            placeholder="Search title, project, or PR number…"
            value={query}
            maxLength={200}
            onChange={(event) => updateControls({ query: event.target.value })}
          />
        </label>
        <div className="filters history-filters">
          <label>
            <span className="sr-only">Filter history status</span>
            <select
              aria-label="Filter history status"
              value={status}
              onChange={(event) =>
                updateControls({ status: event.target.value })
              }
            >
              <option value="">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label>
            <span className="sr-only">Filter history project</span>
            <select
              aria-label="Filter history project"
              value={repositoryId}
              onChange={(event) =>
                updateControls({ repositoryId: event.target.value })
              }
            >
              <option value="">All projects</option>
              {repositories.map((repository) => (
                <option key={repository.id} value={repository.id}>
                  {repository.full_name}
                </option>
              ))}
            </select>
          </label>
          {hasFilters && (
            <button
              className="text-button history-clear"
              onClick={() => setControls(defaultControls(workspaceKey))}
            >
              Clear filters
            </button>
          )}
        </div>
      </div>
      <div
        className="panel history-panel"
        aria-busy={fetching || (!result && !activeError)}
      >
        <div className="list-heading history-list-heading">
          <span>
            {result
              ? `${result.total} ${result.total === 1 ? "run" : "runs"}${hasFilters ? " found" : ""}`
              : "Analysis runs"}
          </span>
          <span>
            Newest first
            {fetching && result && (
              <LoaderCircle
                size={12}
                className="spin"
                aria-label="Updating history"
              />
            )}
          </span>
        </div>
        {activeError ? (
          <div className="history-error" role="alert">
            <CircleAlert size={25} />
            <h2>History couldn't be loaded.</h2>
            <p>{activeError}</p>
            <button className="button secondary" onClick={() => void load()}>
              Retry
            </button>
          </div>
        ) : !result ? (
          <div className="loading-panel" role="status">
            <LoaderCircle size={17} className="spin" aria-hidden="true" />
            Loading analysis history…
          </div>
        ) : !result.items.length ? (
          <div className="empty-state">
            <span className="empty-icon">
              <FileSearch size={26} strokeWidth={1.5} />
            </span>
            <h2>No runs found.</h2>
            <p>
              {hasFilters
                ? "Try another search or clear the filters to see more runs."
                : "Index a project or analyze a change to start your history."}
            </p>
          </div>
        ) : (
          <>
            <div className="history-column-head" aria-hidden="true">
              <span>Run</span>
              <span>Result</span>
              <span>Started</span>
            </div>
            <div className="history-list">
              {result.items.map((run) => (
                <button
                  className="history-row"
                  key={run.id}
                  onClick={() => onOpenRun(run)}
                >
                  <span
                    className={`history-kind-icon ${run.kind}`}
                    aria-hidden="true"
                  >
                    {run.kind === "index" ? (
                      <FolderGit2 size={19} />
                    ) : (
                      <GitPullRequest size={19} />
                    )}
                  </span>
                  <span className="history-run-summary">
                    <strong>
                      {run.title ||
                        (run.kind === "index"
                          ? "Repository indexing"
                          : "Change analysis")}
                    </strong>
                    <span>
                      {run.repository_name}
                      <span className="history-separator">·</span>
                      {run.kind === "index" ? "Indexing" : "Analysis"}
                      {run.kind === "analyze" && run.pr_number > 0 && (
                        <>
                          <span className="history-separator">·</span>PR #
                          {run.pr_number}
                        </>
                      )}
                    </span>
                    {run.error && (
                      <small className="history-run-error">{run.error}</small>
                    )}
                  </span>
                  <span className="history-run-result">
                    <span className={`badge badge-${run.status}`}>
                      {run.status.charAt(0).toUpperCase() + run.status.slice(1)}
                    </span>
                    {run.kind === "analyze" && run.status === "completed" && (
                      <small>
                        {run.alert_count}{" "}
                        {run.alert_count === 1 ? "impact" : "impacts"}
                      </small>
                    )}
                  </span>
                  <span className="history-run-date">
                    <Clock3 size={12} aria-hidden="true" />
                    <time dateTime={run.created_at}>
                      {runDate(run.created_at)}
                    </time>
                  </span>
                  <ChevronRight
                    size={16}
                    className="history-row-arrow"
                    aria-hidden="true"
                  />
                </button>
              ))}
            </div>
          </>
        )}
        {result && result.total > 0 && (
          <div className="history-pagination">
            <p aria-live="polite">
              {result.total
                ? `${offset + 1}–${offset + result.items.length} of ${result.total} runs`
                : "0 runs"}
            </p>
            <div>
              <button
                className="button secondary compact"
                aria-label="Previous page"
                disabled={offset === 0 || fetching}
                onClick={() =>
                  updateControls({ offset: Math.max(0, offset - PAGE_SIZE) })
                }
              >
                <ArrowLeft size={14} />
                Previous
              </button>
              <span className="history-page-number">
                Page {Math.floor(offset / PAGE_SIZE) + 1}
              </span>
              <button
                className="button secondary compact"
                aria-label="Next page"
                disabled={!result.has_more || fetching}
                onClick={() => updateControls({ offset: offset + PAGE_SIZE })}
              >
                Next
                <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
