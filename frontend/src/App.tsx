import type { SupabaseClient } from "@supabase/supabase-js";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  CheckCheck,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Code2,
  ExternalLink,
  FileCode2,
  FileDown,
  FolderGit2,
  GitBranch,
  GitPullRequest,
  Github,
  History,
  Layers3,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { api, friendlyError, safeExternalUrl, setAccessToken } from "./api";
import { AnalysisHistory } from "./AnalysisHistory";
import { downloadRunReport } from "./report";
import type {
  Alert,
  FeedbackKind,
  Integrations,
  Overview,
  PublicConfig,
  Repository,
  Run,
  Session,
} from "./types";

type Page =
  "Dashboard" | "Projects" | "Impact Center" | "Analysis History" | "Settings";
const navigation: { name: Page; icon: typeof Activity }[] = [
  { name: "Dashboard", icon: LayoutDashboard },
  { name: "Projects", icon: FolderGit2 },
  { name: "Impact Center", icon: Layers3 },
  { name: "Analysis History", icon: History },
  { name: "Settings", icon: Settings2 },
];
const pageDescriptions: Record<Page, string> = {
  Dashboard: "A little context. A lot more confidence.",
  Projects: "Your codebase, connected and understood.",
  "Impact Center": "Know what a change touches before it ships.",
  "Analysis History":
    "Search past analyses, follow active runs, and review their results.",
  Settings: "Your workspace, integrations, and privacy.",
};

function SplashMark({ small = false }: { small?: boolean }) {
  return (
    <span className={`splash-mark ${small ? "small" : ""}`} aria-hidden="true">
      <span />
      <span />
      <span />
      <span />
    </span>
  );
}
function Spinner() {
  return <LoaderCircle size={17} className="spin" aria-hidden="true" />;
}
function External({
  href,
  children,
  className,
}: {
  href?: string;
  children: ReactNode;
  className?: string;
}) {
  const url = safeExternalUrl(href);
  return url ? (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
    >
      {children}
    </a>
  ) : (
    <span className={className}>{children}</span>
  );
}
function timeAgo(value: string | null) {
  if (!value) return "Not yet";
  const difference = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(difference)) return "Unknown";
  if (difference < 60_000) return "Just now";
  if (difference < 3_600_000) return `${Math.floor(difference / 60_000)}m ago`;
  if (difference < 86_400_000)
    return `${Math.floor(difference / 3_600_000)}h ago`;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}
function titleCase(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/(^|\s)\w/g, (letter) => letter.toUpperCase());
}
function confidence(value: number) {
  return `${Math.round(value <= 1 ? value * 100 : value)}%`;
}
function Badge({ kind, children }: { kind: string; children?: ReactNode }) {
  return (
    <span className={`badge badge-${kind}`}>{children || titleCase(kind)}</span>
  );
}
function EmptyState({
  icon: Icon = Layers3,
  title,
  description,
  action,
}: {
  icon?: typeof Activity;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon size={26} strokeWidth={1.5} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

function AIAdvisory({ explanation }: { explanation: Alert["ai_explanation"] }) {
  if (!explanation) return null;
  return (
    <section className="ai-advisory">
      <div>
        <Sparkles size={16} />
        <strong>AI hypothesis · needs investigation</strong>
      </div>
      <p className="ai-disclosure">
        Optional hosted reasoning adds suggestions. It has not been
        independently verified and does not change the source verification
        above.
      </p>
      {explanation.summary && <p>{explanation.summary}</p>}
      {explanation.hypotheses?.length ? (
        <ul>
          {explanation.hypotheses.map((item, index) => (
            <li key={index}>
              {item.claim}
              <small>Provided context: {item.citation_ids.join(", ")}</small>
            </li>
          ))}
        </ul>
      ) : null}
      {explanation.recommendations?.length ? (
        <>
          <h4>AI suggestions to review</h4>
          <ul>
            {explanation.recommendations.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      {explanation.model && <small>Model: {explanation.model}</small>}
    </section>
  );
}

function Login({
  client,
  configured,
  onRetry,
}: {
  client: SupabaseClient | null;
  configured: boolean;
  onRetry: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [createAccount, setCreateAccount] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = createAccount
        ? await client.auth.signUp({
            email,
            password,
            options: { emailRedirectTo: window.location.origin },
          })
        : await client.auth.signInWithPassword({ email, password });
      if (result.error) throw result.error;
      if (createAccount && !result.data.session)
        setMessage("Check your email to confirm your account, then sign in.");
    } catch (error) {
      setError(friendlyError(error));
    } finally {
      setBusy(false);
    }
  }
  async function githubSignIn() {
    if (!client) return;
    setBusy(true);
    setError("");
    try {
      const { error } = await client.auth.signInWithOAuth({
        provider: "github",
        options: { redirectTo: window.location.origin },
      });
      if (error) throw error;
    } catch (error) {
      setError(friendlyError(error));
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="login-logo">
          <SplashMark />
          <span>
            splash<span className="brand-dot">.</span>
          </span>
        </div>
        <div>
          <span className="eyebrow">YOUR ENGINEERING TEAMMATE</span>
          <h1>
            Every change
            <br />
            has a ripple.
          </h1>
          <p>
            Know what changed. Understand what it affects.
            <br />
            Ship with your eyes open.
          </p>
          <div className="login-proof">
            <ShieldCheck size={18} /> Read-only access. Evidence you can
            inspect.
          </div>
        </div>
        <span className="login-footnote">Less noise. More understanding.</span>
      </div>
      <div className="login-form-wrap">
        <div className="login-form">
          <span className="eyebrow">WELCOME TO SPLASH</span>
          <h2>
            {createAccount
              ? "Make room for context."
              : "Good to have you back."}
          </h2>
          <p>Sign in to your engineering workspace.</p>
          {!configured ? (
            <div className="setup-message">
              <CircleAlert size={22} />
              <h3>Authentication needs configuration</h3>
              <p>
                Set your Supabase project URL and public anon key on the
                backend. For private local development, start with{" "}
                <code>DEMO_MODE=true</code>.
              </p>
              <button className="button secondary" onClick={onRetry}>
                Check configuration again
              </button>
            </div>
          ) : (
            <>
              <button
                className="button secondary full"
                onClick={githubSignIn}
                disabled={busy}
              >
                <Github size={18} /> Continue with GitHub
              </button>
              <div className="form-divider">
                <span>or use your email</span>
              </div>
              <form onSubmit={submit}>
                <label>
                  Email address
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@yourteam.com"
                    required
                    autoComplete="email"
                  />
                </label>
                <label>
                  Password
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    minLength={8}
                    required
                    autoComplete={
                      createAccount ? "new-password" : "current-password"
                    }
                  />
                </label>
                {error && (
                  <div className="inline-error" role="alert">
                    {error}
                  </div>
                )}
                {message && (
                  <div className="inline-success" role="status">
                    {message}
                  </div>
                )}
                <button
                  className="button primary full"
                  type="submit"
                  disabled={busy}
                >
                  {busy ? <Spinner /> : <ArrowRight size={18} />}
                  {createAccount ? "Create account" : "Sign in"}
                </button>
              </form>
              <button
                className="text-button login-switch"
                onClick={() => {
                  setCreateAccount(!createAccount);
                  setError("");
                  setMessage("");
                }}
              >
                {createAccount
                  ? "Already have an account? Sign in"
                  : "New to Splash? Create an account"}
              </button>
              <p className="auth-note">
                GitHub sign-in must be enabled in your Supabase project.
                Repository access is connected separately.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [bootError, setBootError] = useState("");
  const supabase = useRef<SupabaseClient | null>(null);
  const authSubscription = useRef<(() => void) | null>(null);
  const authEpoch = useRef(0);
  const bootEpoch = useRef(0);
  const workspaceEpoch = useRef(0);
  const refreshEpoch = useRef(0);
  const runRequestEpoch = useRef(0);
  const workspaceIdentity = useRef<string | null>(null);
  const [page, setPage] = useState<Page>("Dashboard");
  const [menuOpen, setMenuOpen] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [loading, setLoading] = useState(false);
  const [dataError, setDataError] = useState("");
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState<{
    message: string;
    error: boolean;
  } | null>(null);
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const selectedRunId = useRef<string | null>(null);
  selectedRunId.current = selectedRun?.id ?? null;
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [deleteRepository, setDeleteRepository] = useState<Repository | null>(
    null,
  );
  const [severity, setSeverity] = useState("all");
  const [status, setStatus] = useState("open");
  const [repoFilter, setRepoFilter] = useState("all");
  const [query, setQuery] = useState("");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const commitSession = useCallback((next: Session | null) => {
    const identity = next ? `${next.user.id}:${next.organization.id}` : null;
    if (workspaceIdentity.current !== identity) {
      workspaceIdentity.current = identity;
      workspaceEpoch.current += 1;
      runRequestEpoch.current += 1;
      setOverview(null);
      setRepositories([]);
      setAlerts([]);
      setRuns([]);
      setIntegrations(null);
      setSelectedAlert(null);
      setSelectedRun(null);
      setHistoryRefreshKey(0);
      setDeleteRepository(null);
      setDataError("");
      setQuery("");
      setSeverity("all");
      setStatus("open");
      setRepoFilter("all");
      setToast(null);
      setBusy("");
      setPage("Dashboard");
    }
    setSession(next);
  }, []);

  const notify = useCallback((message: string, error = false) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, error });
    toastTimer.current = setTimeout(
      () => setToast(null),
      error ? 12_000 : 6_000,
    );
  }, []);

  const initialize = useCallback(async () => {
    const requestEpoch = ++bootEpoch.current;
    authEpoch.current += 1;
    setInitializing(true);
    setBootError("");
    authSubscription.current?.();
    supabase.current = null;
    try {
      const config = await api<PublicConfig>("/public/config");
      if (requestEpoch !== bootEpoch.current) return;
      if (config.mode === "demo") {
        setAccessToken(null);
        const next = await api<Session>("/session");
        if (requestEpoch !== bootEpoch.current) return;
        commitSession(next);
        setAuthReady(true);
      } else {
        const url = config.supabase?.url || import.meta.env.VITE_SUPABASE_URL;
        const key =
          config.supabase?.anon_key || import.meta.env.VITE_SUPABASE_ANON_KEY;
        if (url && key) {
          const { createClient } = await import("@supabase/supabase-js");
          if (requestEpoch !== bootEpoch.current) return;
          const client = createClient(url, key);
          supabase.current = client;
          const { data, error } = await client.auth.getSession();
          if (requestEpoch !== bootEpoch.current) return;
          if (error) throw error;
          setAccessToken(data.session?.access_token || null);
          if (data.session) {
            const next = await api<Session>("/session");
            if (requestEpoch !== bootEpoch.current) return;
            commitSession(next);
          } else commitSession(null);
          const { data: listener } = client.auth.onAuthStateChange(
            (_event, authSession) => {
              const eventEpoch = ++authEpoch.current;
              setAccessToken(authSession?.access_token || null);
              if (!authSession) {
                commitSession(null);
                return;
              }
              if (
                workspaceIdentity.current?.split(":")[0] !== authSession.user.id
              )
                commitSession(null);
              // Do not call the Supabase client inside its auth event callback.
              setTimeout(() => {
                api<Session>("/session")
                  .then((next) => {
                    if (eventEpoch === authEpoch.current) commitSession(next);
                  })
                  .catch((error) => {
                    if (eventEpoch !== authEpoch.current) return;
                    setBootError(friendlyError(error));
                    commitSession(null);
                  });
              }, 0);
            },
          );
          authSubscription.current = () => listener.subscription.unsubscribe();
        } else {
          setAccessToken(null);
          commitSession(null);
        }
        setAuthReady(true);
      }
    } catch (error) {
      if (requestEpoch === bootEpoch.current)
        setBootError(friendlyError(error));
    } finally {
      if (requestEpoch === bootEpoch.current) setInitializing(false);
    }
  }, [commitSession]);
  useEffect(() => {
    void initialize();
    return () => {
      bootEpoch.current += 1;
      authEpoch.current += 1;
      authSubscription.current?.();
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [initialize]);

  const refresh = useCallback(async () => {
    const epoch = workspaceEpoch.current;
    const requestEpoch = ++refreshEpoch.current;
    if (!workspaceIdentity.current) return;
    let results;
    try {
      results = await Promise.all([
        api<Overview>("/overview"),
        api<{ items: Repository[] }>("/repositories"),
        api<{ items: Alert[] }>("/alerts"),
        api<{ items: Run[] }>("/runs"),
        api<Integrations>("/integrations"),
      ]);
    } catch (error) {
      if (
        epoch !== workspaceEpoch.current ||
        requestEpoch !== refreshEpoch.current
      )
        return;
      throw error;
    }
    if (
      epoch !== workspaceEpoch.current ||
      requestEpoch !== refreshEpoch.current
    )
      return;
    setOverview(results[0]);
    setRepositories(results[1].items);
    setAlerts(results[2].items);
    setRuns(results[3].items);
    setIntegrations(results[4]);
    setDataError("");
    return results;
  }, []);
  useEffect(() => {
    if (!session) return;
    let disposed = false;
    setLoading(true);
    refresh()
      .catch((error) => {
        if (!disposed) setDataError(friendlyError(error));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [session, refresh]);
  const isProcessing =
    runs.some((run) => run.status === "queued" || run.status === "running") ||
    repositories.some(
      (repo) => repo.status === "indexing" || repo.status === "pending",
    );
  useEffect(() => {
    if (!session) return;
    const interval = setInterval(
      () => void refresh().catch((error) => setDataError(friendlyError(error))),
      isProcessing ? 1500 : 12000,
    );
    return () => clearInterval(interval);
  }, [session, isProcessing, refresh]);
  useEffect(() => {
    if (!selectedRun || !["queued", "running"].includes(selectedRun.status))
      return;
    const epoch = workspaceEpoch.current;
    const id = selectedRun.id;
    const requestEpoch = runRequestEpoch.current;
    let disposed = false;
    let inFlight = false;
    const interval = setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await api<Run>(`/runs/${encodeURIComponent(id)}`);
        if (
          !disposed &&
          epoch === workspaceEpoch.current &&
          requestEpoch === runRequestEpoch.current &&
          selectedRunId.current === id
        )
          setSelectedRun(result);
      } catch (error) {
        if (
          !disposed &&
          epoch === workspaceEpoch.current &&
          requestEpoch === runRequestEpoch.current &&
          selectedRunId.current === id
        )
          notify(friendlyError(error), true);
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => {
      disposed = true;
      clearInterval(interval);
    };
  }, [selectedRun?.id, selectedRun?.status, notify]);

  async function action(key: string, work: () => Promise<void>) {
    if (busy) return;
    const epoch = workspaceEpoch.current;
    setBusy(key);
    try {
      await work();
    } catch (error) {
      if (epoch === workspaceEpoch.current) notify(friendlyError(error), true);
    } finally {
      if (epoch === workspaceEpoch.current) setBusy("");
    }
  }
  async function analyze(scenario: "rename" | "safe", seed = false) {
    await action(seed ? "seed" : scenario, async () => {
      if (seed) await api("/demo/seed", { method: "POST" });
      await api("/demo/analyze", {
        method: "POST",
        body: JSON.stringify({ scenario }),
      });
      await refresh();
      notify(
        scenario === "rename"
          ? "Breaking change analysis started. Follow the result below."
          : "Safe change analysis started. Checking for meaningful impact.",
      );
    });
  }
  async function connectGithub() {
    const epoch = workspaceEpoch.current;
    await action("github", async () => {
      const result = await api<{ url: string }>("/github/install");
      if (epoch !== workspaceEpoch.current) return;
      const url = safeExternalUrl(result.url);
      if (!url)
        throw new Error(
          "GitHub returned an invalid installation URL. Check your GitHub App configuration.",
        );
      window.location.assign(url);
    });
  }
  async function openAlert(alert: Alert) {
    const epoch = workspaceEpoch.current;
    await action(`alert-${alert.id}`, async () => {
      const result = await api<Alert>(
        `/alerts/${encodeURIComponent(alert.id)}`,
      );
      if (epoch === workspaceEpoch.current) setSelectedAlert(result);
    });
  }
  async function openRun(run: Run) {
    if (busy && !busy.startsWith("run-")) return;
    const epoch = workspaceEpoch.current;
    const requestEpoch = ++runRequestEpoch.current;
    setBusy(`run-${run.id}`);
    try {
      const result = await api<Run>(`/runs/${encodeURIComponent(run.id)}`);
      if (
        epoch === workspaceEpoch.current &&
        requestEpoch === runRequestEpoch.current
      )
        setSelectedRun(result);
    } catch (error) {
      if (
        epoch === workspaceEpoch.current &&
        requestEpoch === runRequestEpoch.current
      )
        notify(friendlyError(error), true);
    } finally {
      if (
        epoch === workspaceEpoch.current &&
        requestEpoch === runRequestEpoch.current
      )
        setBusy("");
    }
  }
  function closeRun() {
    runRequestEpoch.current += 1;
    setSelectedRun(null);
    if (busy.startsWith("run-") || busy === "report") setBusy("");
  }
  async function downloadReport() {
    if (
      !selectedRun ||
      selectedRun.kind !== "analyze" ||
      selectedRun.status !== "completed"
    )
      return;
    const epoch = workspaceEpoch.current;
    const auth = authEpoch.current;
    const requestEpoch = runRequestEpoch.current;
    const id = selectedRun.id;
    const isCurrent = () =>
      epoch === workspaceEpoch.current &&
      auth === authEpoch.current &&
      requestEpoch === runRequestEpoch.current &&
      selectedRunId.current === id;
    await action("report", async () => {
      try {
        await downloadRunReport(id, isCurrent);
      } catch (error) {
        if (isCurrent()) throw error;
      }
    });
  }
  async function feedback(kind: FeedbackKind) {
    if (!selectedAlert) return;
    const epoch = workspaceEpoch.current;
    await action(`feedback-${kind}`, async () => {
      const updated = await api<Alert>(
        `/alerts/${encodeURIComponent(selectedAlert.id)}/feedback`,
        { method: "POST", body: JSON.stringify({ kind }) },
      );
      if (epoch !== workspaceEpoch.current) return;
      setSelectedAlert(updated);
      await refresh();
      notify(
        kind === "useful"
          ? "Thanks. Your feedback helps calibrate future alerts."
          : `Alert marked ${kind === "irrelevant" ? "irrelevant" : "resolved"}.`,
      );
    });
  }
  const goTo = (destination: Page) => {
    closeRun();
    setPage(destination);
    setMenuOpen(false);
    setQuery("");
  };
  const demo = session?.capabilities.demo ?? false;
  const sampleRepository = repositories.find((repo) => repo.is_demo);
  const filteredAlerts = alerts.filter(
    (alert) =>
      (severity === "all" || alert.severity === severity) &&
      (status === "all" || alert.status === status) &&
      (repoFilter === "all" || alert.repository_id === repoFilter) &&
      `${alert.title} ${alert.summary} ${alert.repository_name}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const filteredRepositories = repositories.filter((repo) =>
    `${repo.full_name} ${repo.description}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );

  if (initializing)
    return (
      <div className="boot-screen">
        <SplashMark />
        <span>
          splash<span className="brand-dot">.</span>
        </span>
        <p>
          <Spinner /> Connecting to your workspace
        </p>
      </div>
    );
  if (bootError)
    return (
      <div className="boot-screen">
        <SplashMark />
        <h1>We couldn't connect.</h1>
        <p role="alert">{bootError}</p>
        <button className="button primary" onClick={() => void initialize()}>
          Retry connection <ArrowRight size={16} />
        </button>
        <small>
          Check that the backend is running and its authentication configuration
          is valid.
        </small>
      </div>
    );
  if (!authReady || !session)
    return (
      <Login
        client={supabase.current}
        configured={!!supabase.current}
        onRetry={() => void initialize()}
      />
    );

  function alertCard(alert: Alert) {
    return (
      <button
        key={alert.id}
        className={`alert-card severity-${alert.severity}`}
        onClick={() => void openAlert(alert)}
        disabled={!!busy}
      >
        <div className="alert-icon">
          <CircleAlert size={19} />
        </div>
        <div className="alert-card-body">
          <div className="alert-card-meta">
            <Badge kind={alert.severity} />
            <span>{alert.repository_name}</span>
            <span className="meta-dot">·</span>
            <span>PR #{alert.pr_number}</span>
          </div>
          <h3>{alert.title}</h3>
          <p>{alert.summary}</p>
          <div className="alert-card-bottom">
            <span>
              <FileCode2 size={13} />{" "}
              {alert.affected_component || "Source dependency"}
            </span>
            <span
              className={
                alert.evidence_status === "verified"
                  ? "verified-label"
                  : "investigation-label"
              }
            >
              {alert.evidence_status === "verified" ? (
                <ShieldCheck size={13} />
              ) : (
                <CircleAlert size={13} />
              )}
              {titleCase(alert.evidence_status)}
            </span>
          </div>
        </div>
        <ChevronRight size={18} className="card-chevron" />
      </button>
    );
  }

  return (
    <div className="app-shell">
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <a
          href="#dashboard"
          className="brand"
          onClick={(event) => {
            event.preventDefault();
            goTo("Dashboard");
          }}
        >
          <SplashMark />
          <span>
            splash<span className="brand-dot">.</span>
          </span>
        </a>
        <div className="workspace-switch">
          <span className="workspace-icon">
            {session.organization.name.charAt(0).toUpperCase()}
          </span>
          <span>
            <strong>{session.organization.name}</strong>
            <small>Engineering workspace</small>
          </span>
        </div>
        <span className="nav-caption">WORKSPACE</span>
        <nav aria-label="Main navigation">
          {navigation.map(({ name, icon: Icon }) => (
            <button
              key={name}
              className={`nav-link ${page === name ? "active" : ""}`}
              onClick={() => goTo(name)}
              aria-current={page === name ? "page" : undefined}
            >
              <Icon size={19} strokeWidth={1.7} />
              <span>{name}</span>
              {name === "Impact Center" && !!overview?.open_alerts && (
                <span className="nav-count">{overview.open_alerts}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-promise">
            <span className="promise-icon">
              <ShieldCheck size={19} />
            </span>
            <strong>Context you can trust.</strong>
            <p>Every impact leads back to the source.</p>
            <span>
              <span className="status-dot" />
              Read-only GitHub access
            </span>
          </div>
          <div className="user-profile">
            <span className="avatar">
              {session.user.name?.charAt(0).toUpperCase() || "S"}
            </span>
            <span>
              <strong>{session.user.name || "Splash teammate"}</strong>
              <small>
                {demo ? "Local demo workspace" : session.user.email}
              </small>
            </span>
            {!demo && (
              <button
                className="icon-button"
                aria-label="Sign out"
                onClick={() => void supabase.current?.auth.signOut()}
              >
                <LogOut size={16} />
              </button>
            )}
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMenuOpen(true)}
              aria-label="Open navigation"
            >
              <Menu size={21} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{page}</strong>
          </div>
          <div className="topbar-right">
            {demo && (
              <span className="demo-label">
                <span className="status-dot" />
                Local demo
              </span>
            )}
            <button
              className="notification-button"
              aria-label="View impact alerts"
              onClick={() => goTo("Impact Center")}
            >
              <Bell size={18} />
              {!!overview?.open_alerts && <span />}
            </button>
            <span className="topbar-avatar">
              {session.user.name?.charAt(0).toUpperCase() || "S"}
            </span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {page === "Dashboard"
                  ? "YOUR ENGINEERING PULSE"
                  : page === "Impact Center"
                    ? "FOLLOW THE RIPPLE"
                    : page === "Projects"
                      ? "CONNECTED CODEBASES"
                      : page === "Analysis History"
                        ? "YOUR RUN HISTORY"
                        : "MAKE IT YOURS"}
              </span>
              <h1>
                {page === "Dashboard"
                  ? "The bigger picture."
                  : page === "Impact Center"
                    ? "Impact Center"
                    : page === "Projects"
                      ? "Your projects."
                      : page === "Analysis History"
                        ? "Analysis history"
                        : "Workspace settings."}
              </h1>
              <p>{pageDescriptions[page]}</p>
            </div>
            {page === "Projects" ? (
              <button
                className="button primary"
                onClick={() => void connectGithub()}
                disabled={!!busy}
              >
                <Plus size={17} /> Connect GitHub
              </button>
            ) : page === "Dashboard" ? (
              <span className="live-label">
                <span className="status-dot" />
                {isProcessing ? "Analysis in progress" : "Workspace connected"}
              </span>
            ) : null}
          </div>
          {dataError && (
            <div className="data-error" role="alert">
              <CircleAlert size={19} />
              <span>Couldn't refresh your workspace: {dataError}</span>
              <button
                className="text-button"
                onClick={() =>
                  void action("retry", async () => {
                    await refresh();
                  })
                }
              >
                Retry
              </button>
            </div>
          )}
          {busy.startsWith("run-") && (
            <div className="run-opening-notice" role="status">
              <Spinner />
              Opening run details…
            </div>
          )}
          {loading && !overview ? (
            <div className="panel loading-panel" role="status">
              <Spinner /> Loading your workspace…
            </div>
          ) : (
            <>
              {page === "Dashboard" && (
                <>
                  <section
                    className="overview-grid"
                    aria-label="Workspace metrics"
                  >
                    {[
                      {
                        label: "Connected projects",
                        value: overview?.repositories ?? 0,
                        icon: FolderGit2,
                        note: "Codebases in your workspace",
                        kind: "neutral",
                      },
                      {
                        label: "Open impacts",
                        value: overview?.open_alerts ?? 0,
                        icon: Layers3,
                        note: "Ready for your review",
                        kind: "orange",
                      },
                      {
                        label: "High priority",
                        value: overview?.high_severity_alerts ?? 0,
                        icon: CircleAlert,
                        note: "Worth a closer look",
                        kind: "red",
                      },
                      {
                        label: "Changes analyzed",
                        value: overview?.analyzed_changes ?? 0,
                        icon: GitPullRequest,
                        note: "Context added to every change",
                        kind: "green",
                      },
                    ].map((metric) => (
                      <div
                        className={`metric-card metric-${metric.kind}`}
                        key={metric.label}
                      >
                        <div className="metric-heading">
                          <span>{metric.label}</span>
                          <metric.icon size={18} strokeWidth={1.6} />
                        </div>
                        <strong>
                          {metric.value}
                          <span>
                            {metric.kind === "orange" && metric.value > 0
                              ? "to review"
                              : ""}
                          </span>
                        </strong>
                        <small>{metric.note}</small>
                      </div>
                    ))}
                  </section>
                  {demo && (
                    <section className="demo-banner">
                      <div className="demo-banner-art" aria-hidden="true">
                        <span />
                        <span />
                        <span />
                        <Code2 size={25} />
                      </div>
                      <div>
                        <span className="eyebrow">
                          A SMALL CHANGE. A REAL RIPPLE.
                        </span>
                        <h2>
                          {sampleRepository
                            ? "See both sides of a change."
                            : "Meet your new engineering teammate."}
                        </h2>
                        <p>
                          {sampleRepository
                            ? "Compare a renamed API response field with a safe edit. Splash traces the effect on the interface and shows the source evidence."
                            : "Try a sample FastAPI and TypeScript project. Rename a response field and see where the interface still reads its old name."}
                        </p>
                        <div className="demo-banner-notes">
                          <span>
                            <Check size={13} /> Real source analysis
                          </span>
                          <span>
                            <Check size={13} /> No API key needed
                          </span>
                          <span>
                            <Check size={13} /> Free to run locally
                          </span>
                        </div>
                      </div>
                      <div className="demo-actions">
                        {sampleRepository ? (
                          <>
                            <button
                              className="button primary"
                              disabled={!!busy || isProcessing}
                              onClick={() => void analyze("rename")}
                            >
                              {busy === "rename" ? (
                                <Spinner />
                              ) : (
                                <Zap size={16} />
                              )}{" "}
                              Analyze breaking change
                            </button>
                            <button
                              className="button secondary"
                              disabled={!!busy || isProcessing}
                              onClick={() => void analyze("safe")}
                            >
                              {busy === "safe" ? (
                                <Spinner />
                              ) : (
                                <ShieldCheck size={16} />
                              )}{" "}
                              Analyze safe change
                            </button>
                          </>
                        ) : (
                          <button
                            className="button primary"
                            disabled={!!busy}
                            onClick={() => void analyze("rename", true)}
                          >
                            {busy === "seed" ? (
                              <Spinner />
                            ) : (
                              <Sparkles size={16} />
                            )}{" "}
                            Try sample project <ArrowRight size={16} />
                          </button>
                        )}
                      </div>
                    </section>
                  )}
                  <div className="dashboard-columns">
                    <section className="panel impact-panel">
                      <div className="panel-heading">
                        <div>
                          <h2>
                            Needs your attention{" "}
                            <span className="count-pill">
                              {overview?.open_alerts ?? 0}
                            </span>
                          </h2>
                          <p>The most relevant ripples in your codebase.</p>
                        </div>
                        <button
                          className="text-button"
                          onClick={() => goTo("Impact Center")}
                        >
                          View all <ArrowUpRight size={15} />
                        </button>
                      </div>
                      <div className="alert-list">
                        {overview?.recent_alerts.filter(
                          (alert) => alert.status === "open",
                        ).length ? (
                          overview.recent_alerts
                            .filter((alert) => alert.status === "open")
                            .slice(0, 4)
                            .map(alertCard)
                        ) : (
                          <EmptyState
                            icon={CheckCheck}
                            title="A clear view ahead."
                            description={
                              repositories.length
                                ? "No open impacts to review. New findings appear here when a pull request is analyzed."
                                : "Connect a project or try the sample to start understanding the impact of changes."
                            }
                          />
                        )}
                      </div>
                      <div className="panel-footnote">
                        <ShieldCheck size={14} /> Source evidence first. Your
                        judgment always.
                      </div>
                    </section>
                    <section className="panel activity-panel">
                      <div className="panel-heading">
                        <div>
                          <h2>Analysis activity</h2>
                          <p>From change to understanding.</p>
                        </div>
                        <button
                          className="text-button"
                          onClick={() => goTo("Analysis History")}
                        >
                          View history <ArrowUpRight size={15} />
                        </button>
                      </div>
                      <div className="activity-list">
                        {runs.length ? (
                          runs.slice(0, 5).map((run) => (
                            <button
                              className="activity-item"
                              key={run.id}
                              onClick={() => void openRun(run)}
                            >
                              <span
                                className={`activity-icon ${run.status}`}
                                aria-hidden="true"
                              >
                                {run.status === "completed" ? (
                                  <Check size={16} />
                                ) : run.status === "failed" ? (
                                  <X size={16} />
                                ) : (
                                  <LoaderCircle size={16} className="spin" />
                                )}
                              </span>
                              <span className="activity-body">
                                <strong>
                                  {run.title ||
                                    (run.kind === "index"
                                      ? "Repository indexing"
                                      : "Change analysis")}
                                </strong>
                                <span>{run.repository_name}</span>
                                <small>
                                  <Badge kind={run.status} />
                                  {run.status === "completed" &&
                                    run.kind === "analyze" &&
                                    `${run.alert_count} ${run.alert_count === 1 ? "impact" : "impacts"}`}
                                  <span>{timeAgo(run.created_at)}</span>
                                </small>
                              </span>
                              <ChevronRight size={14} />
                            </button>
                          ))
                        ) : (
                          <EmptyState
                            icon={Activity}
                            title="Quiet for now."
                            description="Your analysis timeline starts with the first change."
                          />
                        )}
                      </div>
                      <div className="activity-bottom">
                        <span className="status-dot" />
                        {integrations?.openai.enabled
                          ? "AI-assisted reasoning enabled"
                          : "Local analysis · no AI spend"}
                      </div>
                    </section>
                  </div>
                  <div className="context-note">
                    <span className="context-note-icon">
                      <ArrowDownLeft size={17} />
                    </span>
                    <p>
                      <strong>The details make the difference.</strong> Splash
                      traces changes through your code and brings the evidence
                      to you.
                    </p>
                    <button
                      className="text-button"
                      onClick={() => goTo("Settings")}
                    >
                      How it works <ArrowRight size={14} />
                    </button>
                  </div>
                </>
              )}
              {page === "Projects" && (
                <>
                  <div className="section-toolbar">
                    <label className="search-field">
                      <Search size={17} />
                      <input
                        aria-label="Search projects"
                        placeholder="Find a project…"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </label>
                    <span className="result-count">
                      {repositories.length}{" "}
                      {repositories.length === 1 ? "project" : "projects"}{" "}
                      connected
                    </span>
                  </div>
                  {filteredRepositories.length ? (
                    <div className="project-grid">
                      {filteredRepositories.map((repository) => (
                        <article
                          className="panel project-card"
                          key={repository.id}
                        >
                          <div className="project-card-top">
                            <span className="repo-icon">
                              <FolderGit2 size={24} strokeWidth={1.5} />
                            </span>
                            <Badge kind={repository.status} />
                          </div>
                          <div className="project-name">
                            <External href={repository.html_url}>
                              {repository.full_name}
                              <ArrowUpRight size={15} />
                            </External>
                            {repository.is_demo && (
                              <span className="sample-tag">SAMPLE</span>
                            )}
                          </div>
                          <p>
                            {repository.description ||
                              "Connected GitHub repository."}
                          </p>
                          <div className="project-meta">
                            <span>
                              <span
                                className={`language-dot ${repository.language.toLowerCase()}`}
                              />
                              {repository.language || "Mixed"}
                            </span>
                            <span>
                              <GitBranch size={14} />
                              {repository.default_branch}
                            </span>
                          </div>
                          <div className="project-stats">
                            <span>
                              <strong>{repository.indexed_files}</strong>files
                              indexed
                            </span>
                            <span>
                              <strong>{repository.indexed_chunks}</strong>source
                              chunks
                            </span>
                          </div>
                          <div className="project-card-footer">
                            <small>
                              {repository.last_indexed_at
                                ? `Indexed ${timeAgo(repository.last_indexed_at).toLowerCase()}`
                                : "Awaiting first index"}
                            </small>
                            <div>
                              <button
                                className="icon-button"
                                aria-label={`Delete ${repository.full_name}`}
                                onClick={() => setDeleteRepository(repository)}
                                disabled={!!busy}
                              >
                                <Trash2 size={16} />
                              </button>
                              <button
                                className="button secondary compact"
                                disabled={
                                  !!busy || repository.status === "indexing"
                                }
                                onClick={() =>
                                  void action(
                                    `index-${repository.id}`,
                                    async () => {
                                      await api(
                                        `/repositories/${encodeURIComponent(repository.id)}/index`,
                                        { method: "POST" },
                                      );
                                      await refresh();
                                      notify(
                                        "Indexing started. Your project status will update automatically.",
                                      );
                                    },
                                  )
                                }
                              >
                                {busy === `index-${repository.id}` ||
                                repository.status === "indexing" ? (
                                  <Spinner />
                                ) : (
                                  <Activity size={14} />
                                )}
                                {repository.status === "indexing"
                                  ? "Indexing"
                                  : "Reindex"}
                              </button>
                            </div>
                          </div>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <section className="panel">
                      <EmptyState
                        icon={FolderGit2}
                        title={
                          query
                            ? "No projects match."
                            : "Your next insight starts here."
                        }
                        description={
                          query
                            ? "Try a different name or clear your search."
                            : "Connect your GitHub App to index a repository. Splash only reads supported source files."
                        }
                        action={
                          query ? (
                            <button
                              className="button secondary"
                              onClick={() => setQuery("")}
                            >
                              Clear search
                            </button>
                          ) : (
                            <div className="empty-actions">
                              <button
                                className="button primary"
                                onClick={() => void connectGithub()}
                                disabled={!!busy}
                              >
                                <Github size={17} />
                                Connect GitHub
                              </button>
                              {demo && (
                                <button
                                  className="button secondary"
                                  onClick={() => void analyze("rename", true)}
                                  disabled={!!busy}
                                >
                                  Try sample project
                                </button>
                              )}
                            </div>
                          )
                        }
                      />
                    </section>
                  )}
                  <div className="info-strip">
                    <ShieldCheck size={20} />
                    <div>
                      <strong>A connection, with boundaries.</strong>
                      <p>
                        Read-only access. Python, JavaScript, and TypeScript
                        source indexing. Remove a project to delete its indexed
                        data, analysis history, alerts, and feedback from
                        Splash.
                      </p>
                    </div>
                  </div>
                </>
              )}
              {page === "Impact Center" && (
                <>
                  <div className="impact-summary">
                    <span>
                      <span className="summary-dot high" />
                      {
                        alerts.filter(
                          (alert) =>
                            alert.severity === "high" &&
                            alert.status === "open",
                        ).length
                      }{" "}
                      high priority
                    </span>
                    <span>
                      <span className="summary-dot medium" />
                      {
                        alerts.filter(
                          (alert) =>
                            alert.severity === "medium" &&
                            alert.status === "open",
                        ).length
                      }{" "}
                      medium priority
                    </span>
                    <span>
                      <span className="summary-dot low" />
                      {
                        alerts.filter(
                          (alert) =>
                            alert.severity === "low" && alert.status === "open",
                        ).length
                      }{" "}
                      low priority
                    </span>
                    <span className="summary-help">
                      <ShieldCheck size={14} /> Evidence attached to every
                      finding
                    </span>
                  </div>
                  <div className="section-toolbar impact-toolbar">
                    <label className="search-field">
                      <Search size={17} />
                      <input
                        aria-label="Search impacts"
                        placeholder="Search impacts…"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </label>
                    <div className="filters">
                      <label>
                        <span className="sr-only">Filter severity</span>
                        <select
                          aria-label="Filter severity"
                          value={severity}
                          onChange={(event) => setSeverity(event.target.value)}
                        >
                          <option value="all">All priorities</option>
                          <option value="high">High priority</option>
                          <option value="medium">Medium priority</option>
                          <option value="low">Low priority</option>
                        </select>
                      </label>
                      <label>
                        <span className="sr-only">Filter status</span>
                        <select
                          aria-label="Filter status"
                          value={status}
                          onChange={(event) => setStatus(event.target.value)}
                        >
                          <option value="open">Open impacts</option>
                          <option value="resolved">Resolved</option>
                          <option value="dismissed">Dismissed</option>
                          <option value="all">All statuses</option>
                        </select>
                      </label>
                      <label>
                        <span className="sr-only">Filter project</span>
                        <select
                          aria-label="Filter project"
                          value={repoFilter}
                          onChange={(event) =>
                            setRepoFilter(event.target.value)
                          }
                        >
                          <option value="all">All projects</option>
                          {repositories.map((repo) => (
                            <option value={repo.id} key={repo.id}>
                              {repo.full_name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </div>
                  <section className="panel all-impacts">
                    <div className="list-heading">
                      <span>
                        {filteredAlerts.length}{" "}
                        {filteredAlerts.length === 1 ? "impact" : "impacts"}
                      </span>
                      <span>Sorted by recent activity</span>
                    </div>
                    {filteredAlerts.length ? (
                      filteredAlerts.map(alertCard)
                    ) : (
                      <EmptyState
                        icon={CheckCheck}
                        title="Nothing needs your attention here."
                        description="No findings match these filters. Try all statuses, or analyze a new change."
                        action={
                          <button
                            className="button secondary"
                            onClick={() => {
                              setStatus("all");
                              setSeverity("all");
                              setRepoFilter("all");
                              setQuery("");
                            }}
                          >
                            Reset filters
                          </button>
                        }
                      />
                    )}
                  </section>
                </>
              )}
              {page === "Analysis History" && (
                <AnalysisHistory
                  workspaceKey={`${session.user.id}:${session.organization.id}`}
                  repositories={repositories}
                  onOpenRun={(run) => void openRun(run)}
                  refreshKey={historyRefreshKey}
                />
              )}
              {page === "Settings" && (
                <div className="settings-stack">
                  <section className="panel settings-panel">
                    <div className="panel-heading">
                      <div>
                        <h2>Connected services</h2>
                        <p>Clear boundaries. Real connection status.</p>
                      </div>
                    </div>
                    <div className="integration-row">
                      <span className="integration-icon github">
                        <Github size={25} />
                      </span>
                      <div>
                        <h3>GitHub App</h3>
                        <p>
                          {integrations?.github.connected
                            ? "Repositories connected with read-only permissions."
                            : integrations?.github.configured
                              ? "Your app is configured. Install it on selected repositories."
                              : "Connect a read-only GitHub App to analyze your pull requests."}
                        </p>
                        {integrations?.github.app_name && (
                          <small>{integrations.github.app_name}</small>
                        )}
                      </div>
                      <Badge
                        kind={
                          integrations?.github.connected
                            ? "ready"
                            : integrations?.github.configured
                              ? "pending"
                              : "unconfigured"
                        }
                      >
                        {integrations?.github.connected
                          ? "Connected"
                          : integrations?.github.configured
                            ? "Ready to connect"
                            : "Not configured"}
                      </Badge>
                      <button
                        className="button secondary compact"
                        onClick={() => void connectGithub()}
                        disabled={!!busy}
                      >
                        Connect <ArrowUpRight size={14} />
                      </button>
                    </div>
                    {!integrations?.github.configured && (
                      <div className="integration-guide">
                        <strong>Set up your GitHub App</strong>
                        <p>
                          Create an app in GitHub developer settings with read
                          access to Contents, Pull requests, and Metadata.
                          Subscribe to pull request and installation events.
                          Configure the backend app ID, private key, webhook
                          secret, and installation URL in your local environment
                          file. Use the setup guide in the repository for the
                          full steps.
                        </p>
                        <External
                          href="https://github.com/settings/apps/new"
                          className="text-link"
                        >
                          Open GitHub App settings <ExternalLink size={13} />
                        </External>
                      </div>
                    )}
                    <div className="integration-row">
                      <span className="integration-icon ai">
                        <Sparkles size={25} />
                      </span>
                      <div>
                        <h3>OpenAI</h3>
                        <p>
                          {integrations?.openai.enabled
                            ? `Opt-in AI reasoning with ${integrations.openai.model}.`
                            : "Optional reasoning support. Core source analysis runs for free."}
                        </p>
                      </div>
                      <Badge
                        kind={
                          integrations?.openai.enabled ? "ready" : "neutral"
                        }
                      >
                        {integrations?.openai.enabled ? "Enabled" : "Off"}
                      </Badge>
                    </div>
                    <div className="integration-row">
                      <span className="integration-icon auth">
                        <ShieldCheck size={25} />
                      </span>
                      <div>
                        <h3>Supabase Auth</h3>
                        <p>
                          {demo
                            ? "This isolated local demo uses a sample identity."
                            : "Your workspace uses verified Supabase sessions."}
                        </p>
                      </div>
                      <Badge
                        kind={
                          integrations?.supabase.configured
                            ? "ready"
                            : "neutral"
                        }
                      >
                        {integrations?.supabase.configured
                          ? "Configured"
                          : "Local demo"}
                      </Badge>
                    </div>
                  </section>
                  <div className="settings-columns">
                    <section className="panel privacy-panel">
                      <span className="settings-section-icon">
                        <ShieldCheck size={25} />
                      </span>
                      <h2>Your code. Your choice.</h2>
                      <p>
                        Hosted AI is opt-in and off by default. Without it,
                        Splash uses structural analysis and local source
                        retrieval.
                      </p>
                      <div className="privacy-item">
                        <Check size={16} />
                        <span>
                          <strong>What can leave your machine</strong>When
                          enabled, selected source excerpts, changed hunks,
                          paths, and dependency context may be sent to OpenAI
                          for embeddings or reasoning.
                        </span>
                      </div>
                      <div className="privacy-item">
                        <Check size={16} />
                        <span>
                          <strong>Bounded by configuration</strong>Enable hosted
                          AI and set request limits on the backend. Never paste
                          API keys into the browser.
                        </span>
                      </div>
                      <div className="privacy-item">
                        <Check size={16} />
                        <span>
                          <strong>Read-only by design</strong>Splash never runs
                          your repository code or modifies it. You inspect the
                          evidence and make the decision.
                        </span>
                      </div>
                      <div className="privacy-status">
                        <span className="status-dot" />
                        Hosted AI is currently{" "}
                        {integrations?.openai.enabled ? "enabled" : "disabled"}
                      </div>
                    </section>
                    <section className="panel workspace-settings">
                      <span className="settings-section-icon">
                        <Settings2 size={25} />
                      </span>
                      <h2>Workspace details</h2>
                      <dl>
                        <div>
                          <dt>Workspace</dt>
                          <dd>{session.organization.name}</dd>
                        </div>
                        <div>
                          <dt>Signed in as</dt>
                          <dd>{session.user.email}</dd>
                        </div>
                        <div>
                          <dt>Environment</dt>
                          <dd>
                            {demo
                              ? "Private local demo"
                              : titleCase(session.mode)}
                          </dd>
                        </div>
                        <div>
                          <dt>Analysis languages</dt>
                          <dd>Python · JavaScript · TypeScript</dd>
                        </div>
                      </dl>
                      <div className="workspace-disclosure">
                        <CircleAlert size={18} />
                        <p>
                          {demo
                            ? "Demo mode is for private local use. Disable it and configure Supabase before any public deployment."
                            : "Only members of this workspace can access its repositories, analysis, and alerts."}
                        </p>
                      </div>
                    </section>
                  </div>
                </div>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>
              <SplashMark small /> A little more context. A little less
              guesswork.
            </span>
            <span>Splash · V1</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div
          className={`toast ${toast.error ? "error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          {toast.error ? <CircleAlert size={19} /> : <CircleCheck size={19} />}
          <span>{toast.message}</span>
          <button
            className="icon-button"
            aria-label="Dismiss notification"
            onClick={() => setToast(null)}
          >
            <X size={17} />
          </button>
        </div>
      )}
      {selectedAlert && (
        <Dialog
          onClose={() => setSelectedAlert(null)}
          title="Source evidence"
          className="evidence-dialog"
        >
          <div className="dialog-eyebrow">
            <Badge kind={selectedAlert.severity} />
            <span>
              {selectedAlert.repository_name} · PR #{selectedAlert.pr_number}
            </span>
          </div>
          <h2>{selectedAlert.title}</h2>
          <p className="dialog-summary">{selectedAlert.summary}</p>
          <div className="evidence-facts">
            <div>
              <span>Verification</span>
              <strong
                className={
                  selectedAlert.evidence_status === "verified"
                    ? "verified-label"
                    : "investigation-label"
                }
              >
                <ShieldCheck size={14} />
                {titleCase(selectedAlert.evidence_status)}
              </strong>
            </div>
            <div>
              <span>Confidence</span>
              <strong>{confidence(selectedAlert.confidence)}</strong>
            </div>
            <div>
              <span>Alert status</span>
              <Badge kind={selectedAlert.status} />
            </div>
          </div>
          <h3 className="dialog-section-title">Follow the source</h3>
          <p className="section-helper">
            Citations refer to the exact analyzed snapshot. Review the
            dependency before acting.
          </p>
          <div className="evidence-blocks">
            {selectedAlert.evidence.length ? (
              selectedAlert.evidence.map((evidence, index) => (
                <article
                  className="evidence-block"
                  key={`${evidence.path}-${index}`}
                >
                  <div className="evidence-file">
                    <FileCode2 size={15} />
                    <strong>{evidence.path}</strong>
                    <span>
                      Lines {evidence.start_line}–{evidence.end_line}
                    </span>
                  </div>
                  <div className="evidence-role">
                    <Badge kind="neutral">
                      {titleCase(evidence.role || "Source")}
                    </Badge>
                    <code>
                      {evidence.commit_sha
                        ? evidence.commit_sha.slice(0, 8)
                        : "Local snapshot"}
                    </code>
                  </div>
                  <pre>
                    <code>{evidence.snippet}</code>
                  </pre>
                  {safeExternalUrl(evidence.source_url) && (
                    <External
                      href={evidence.source_url}
                      className="source-link"
                    >
                      View exact source <ExternalLink size={13} />
                    </External>
                  )}
                </article>
              ))
            ) : (
              <div className="inline-error">
                Source evidence was not available for this finding. Treat it as
                needing investigation.
              </div>
            )}
          </div>
          <AIAdvisory explanation={selectedAlert.ai_explanation} />
          {selectedAlert.recommendations.length > 0 && (
            <>
              <h3 className="dialog-section-title">Suggested next steps</h3>
              <ul className="recommendations">
                {selectedAlert.recommendations.map((recommendation, index) => (
                  <li key={index}>
                    <ArrowRight size={14} />
                    <span>{recommendation}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {safeExternalUrl(selectedAlert.pr_url) && (
            <External href={selectedAlert.pr_url} className="text-link">
              Review pull request #{selectedAlert.pr_number}{" "}
              <ExternalLink size={14} />
            </External>
          )}
          <div className="feedback-bar">
            <div>
              <strong>Was this useful?</strong>
              <span>Your feedback keeps the signal relevant.</span>
            </div>
            <div>
              <button
                className={`button secondary compact ${selectedAlert.feedback === "useful" ? "selected" : ""}`}
                onClick={() => void feedback("useful")}
                disabled={!!busy}
              >
                <Check size={14} />
                Mark useful
              </button>
              <button
                className="button secondary compact"
                onClick={() => void feedback("irrelevant")}
                disabled={!!busy || selectedAlert.status === "dismissed"}
              >
                Mark irrelevant
              </button>
              <button
                className="button primary compact"
                onClick={() => void feedback("resolved")}
                disabled={!!busy || selectedAlert.status === "resolved"}
              >
                <CheckCheck size={14} />
                Mark resolved
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {selectedRun && (
        <Dialog
          onClose={closeRun}
          title="Analysis details"
          className="run-dialog"
        >
          <div className="dialog-eyebrow">
            <Badge kind={selectedRun.status} />
            <span>
              {selectedRun.repository_name} ·{" "}
              {selectedRun.kind === "index" ? "Indexing" : "Analysis"}
              {selectedRun.kind === "analyze" && selectedRun.pr_number > 0
                ? ` · PR #${selectedRun.pr_number}`
                : ""}
            </span>
          </div>
          <h2>
            {selectedRun.title ||
              (selectedRun.kind === "index"
                ? "Repository indexing"
                : "Change analysis")}
          </h2>
          <p className="dialog-summary">
            {selectedRun.kind === "index"
              ? "Indexing prepares source files for future change analysis."
              : "Review the change analysis and the evidence behind its findings."}
          </p>
          <div className="run-result">
            {selectedRun.kind === "analyze" &&
            selectedRun.status === "completed" ? (
              <>
                <strong>{selectedRun.alert_count}</strong>
                <span>
                  {selectedRun.alert_count === 1
                    ? "impact found"
                    : "impacts found"}
                </span>
              </>
            ) : (
              <span className="run-progress-label">
                {selectedRun.kind === "index"
                  ? selectedRun.status === "completed"
                    ? "Indexing completed"
                    : selectedRun.status === "failed"
                      ? "Indexing did not complete"
                      : selectedRun.status === "queued"
                        ? "Indexing queued"
                        : "Indexing in progress"
                  : selectedRun.status === "failed"
                    ? "Analysis did not complete"
                    : selectedRun.status === "queued"
                      ? "Analysis queued"
                      : "Analysis in progress"}
              </span>
            )}
            <span>{timeAgo(selectedRun.created_at)}</span>
          </div>
          {(selectedRun.base_sha || selectedRun.head_sha) &&
            selectedRun.kind === "analyze" && (
              <dl className="run-snapshots">
                {selectedRun.base_sha && (
                  <div>
                    <dt>Base snapshot</dt>
                    <dd>
                      <code>{selectedRun.base_sha}</code>
                    </dd>
                  </div>
                )}
                {selectedRun.head_sha && (
                  <div>
                    <dt>Head snapshot</dt>
                    <dd>
                      <code>{selectedRun.head_sha}</code>
                    </dd>
                  </div>
                )}
              </dl>
            )}
          {selectedRun.error && (
            <div className="inline-error" role="alert">
              {selectedRun.error}
            </div>
          )}
          {selectedRun.status === "failed" && (
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() =>
                void action("retry-run", async () => {
                  const epoch = workspaceEpoch.current;
                  const requestEpoch = runRequestEpoch.current;
                  const id = selectedRun.id;
                  await api(`/runs/${encodeURIComponent(id)}/retry`, {
                    method: "POST",
                  });
                  if (
                    epoch !== workspaceEpoch.current ||
                    requestEpoch !== runRequestEpoch.current
                  )
                    return;
                  const updated = await api<Run>(
                    `/runs/${encodeURIComponent(id)}`,
                  );
                  if (
                    epoch !== workspaceEpoch.current ||
                    requestEpoch !== runRequestEpoch.current
                  )
                    return;
                  setSelectedRun(updated);
                  setHistoryRefreshKey((key) => key + 1);
                  await refresh();
                  notify("Run queued for retry.");
                })
              }
            >
              {selectedRun.kind === "index"
                ? "Retry indexing"
                : "Retry analysis"}{" "}
              <ArrowRight size={14} />
            </button>
          )}
          <div className="run-stages">
            {selectedRun.stages.length ? (
              selectedRun.stages.map((stage, index) => (
                <div className="run-stage" key={index}>
                  <span className="stage-number">
                    {stage.status === "completed" ? (
                      <Check size={15} />
                    ) : (
                      index + 1
                    )}
                  </span>
                  <div>
                    <h3>
                      {titleCase(stage.name)} <Badge kind={stage.status} />
                    </h3>
                    <p>
                      {stage.summary ||
                        (stage.status === "completed"
                          ? "Stage completed."
                          : stage.status === "failed"
                            ? "Stage did not complete."
                            : "Waiting for stage results.")}
                    </p>
                  </div>
                  <small>
                    {stage.duration_ms
                      ? `${(stage.duration_ms / 1000).toFixed(2)}s`
                      : ""}
                  </small>
                </div>
              ))
            ) : ["queued", "running"].includes(selectedRun.status) ? (
              <div className="loading-panel">
                <Spinner /> Waiting for stage results…
              </div>
            ) : (
              <p className="run-no-trace">
                No stage trace was recorded for this run.
              </p>
            )}
          </div>
          {selectedRun.kind === "analyze" &&
            selectedRun.status === "completed" && (
              <div className="run-detail-actions">
                <button
                  className="button primary"
                  onClick={() => {
                    setStatus("all");
                    goTo("Impact Center");
                  }}
                >
                  Open Impact Center <ArrowRight size={15} />
                </button>
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={() => void downloadReport()}
                >
                  {busy === "report" ? <Spinner /> : <FileDown size={16} />}
                  Download report
                </button>
              </div>
            )}
        </Dialog>
      )}
      {deleteRepository && (
        <Dialog
          onClose={() => {
            if (!busy) setDeleteRepository(null);
          }}
          title="Remove project"
          className="delete-dialog"
        >
          <span className="delete-icon">
            <Trash2 size={25} />
          </span>
          <h2>Remove this project?</h2>
          <p className="dialog-summary">
            <strong>{deleteRepository.full_name}</strong> and its indexed source
            data, analysis history, alerts, and feedback will be removed from
            this workspace. Your GitHub repository stays intact.
          </p>
          <div className="dialog-actions">
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => setDeleteRepository(null)}
            >
              Keep project
            </button>
            <button
              className="button danger"
              disabled={!!busy}
              onClick={() =>
                void action("delete", async () => {
                  await api(
                    `/repositories/${encodeURIComponent(deleteRepository.id)}`,
                    { method: "DELETE" },
                  );
                  setDeleteRepository(null);
                  await refresh();
                  notify("Project and associated indexed data removed.");
                })
              }
            >
              {busy === "delete" ? <Spinner /> : <Trash2 size={16} />}Remove
              project
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function Dialog({
  title,
  children,
  onClose,
  className = "",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  className?: string;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () =>
      dialog.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input, select, [tabindex="0"]',
      );
    focusable()?.[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
      if (event.key !== "Tab") return;
      const elements = focusable();
      if (!elements?.length) return;
      const first = elements[0],
        last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = bodyOverflow;
      document.removeEventListener("keydown", keydown);
      previousFocus?.focus();
    };
  }, []);
  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        className={`dialog ${className}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="dialog-topline">
          <span>{title}</span>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
