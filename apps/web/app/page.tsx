"use client";

import {
  Suspense,
  useState,
  useId,
  useRef,
  useEffect,
  cloneElement,
  type ReactElement,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Laptop,
  LayoutDashboard,
  LogOut,
  Pause,
  Play,
  Plus,
  Radio,
  Search,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import QRCode from "qrcode";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { api, ApiError, currentUser } from "./api";

type User = {
  id: string;
  email: string;
  mfa_enabled: boolean;
  password_enabled: boolean;
  google_connected: boolean;
  session_id: string;
};
type Monitor = {
  id: string;
  name: string;
  url: string;
  method: string;
  statuses: number[];
  interval: number;
  timeout: number;
  paused: boolean;
  archived_at: string | null;
  state: string;
  last_observed: string | null;
  observed_state: string;
};
type Result = {
  id: string;
  observed_at: string;
  success: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
};
type Incident = { id: string; opened_at: string; closed_at: string | null };
type Metrics = {
  window_start: string;
  window_end: string;
  retention_clipped: boolean;
  completed: number;
  successful: number;
  failed: number;
  expected: number;
  missing: number;
  uptime: number | null;
  coverage: number | null;
  median_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  histogram: { range: string; count: number }[];
  latency_bucket_seconds: number;
  latency_trend: { starts_at: string; median_ms: number | null }[];
};
type Channel = {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  email: string | null;
};
type Delivery = {
  id: string;
  channel_id: string;
  event_kind: string;
  attempts: number;
  delivered_at: string | null;
  exhausted: boolean;
  last_error: string | null;
  next_attempt: string;
  test_queued_at: string | null;
};
type BrowserSession = {
  id: string;
  label: string;
  created_at: string;
  last_activity: string;
  current: boolean;
};
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchIntervalInBackground: false,
    },
  },
});
const stamp = (s: string | null) =>
  s ? new Date(s).toLocaleString() : "No checks yet";
const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(2)}%`;
const ms = (v: number | null | undefined) =>
  v == null ? "—" : `${Math.round(v)} ms`;
const message = (e: unknown) =>
  e instanceof Error ? e.message : "Something went wrong";
function useData<T>(key: string, url: string, enabled = true) {
  return useQuery<T>({
    queryKey: [key, url],
    queryFn: () => api<T>(url),
    enabled,
    refetchInterval:
      /^(dashboard|worker-status|monitors|deliveries)([/?]|$)/.test(
        url.slice(1),
      )
        ? 5000
        : false,
  });
}
function Badge({ state, label }: { state: string; label?: string }) {
  return (
    <span className={`badge ${state.toLowerCase()}`}>
      <i />
      {label ||
        (
          {
            UP: "Working",
            DOWN: "Down",
            SUSPECT: "Possible problem",
            UNKNOWN: "No recent checks",
            PAUSED: "Paused",
          } as Record<string, string>
        )[state] ||
        state}
    </span>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {cloneElement(children as ReactElement<{ id?: string }>, { id })}
    </div>
  );
}
function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (node && !node.contains(document.activeElement))
      node.querySelector<HTMLElement>("input, select, button")?.focus();
  }, []);
  return (
    <div className="overlay" onClick={close}>
      <section
        className="modal"
        ref={dialog}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            close();
          }
          if (e.key !== "Tab") return;
          const controls = Array.from(
            dialog.current?.querySelectorAll<HTMLElement>(
              "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]",
            ) || [],
          );
          const first = controls[0],
            last = controls[controls.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button className="icon" aria-label="Close dialog" onClick={close}>
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Radio size={30} />
      <p>{children}</p>
    </div>
  );
}

export default function Page() {
  return (
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<div className="loading">Loading PulseGrid…</div>}>
        <Application />
      </Suspense>
    </QueryClientProvider>
  );
}
function Application() {
  const client = useQueryClient();
  useEffect(() => {
    if (
      new URLSearchParams(window.location.hash.slice(1)).has("google_success")
    )
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
  }, []);
  const user = useQuery<User>({
    queryKey: ["me"],
    queryFn: () => currentUser<User>(),
    refetchInterval: (query) =>
      query.state.data &&
      !(
        query.state.error instanceof ApiError &&
        query.state.error.status === 401
      )
        ? 10000
        : false,
    refetchOnWindowFocus: (query) =>
      !!query.state.data &&
      !(
        query.state.error instanceof ApiError &&
        query.state.error.status === 401
      ),
    refetchOnReconnect: (query) =>
      !!query.state.data &&
      !(
        query.state.error instanceof ApiError &&
        query.state.error.status === 401
      ),
  });
  if (user.isPending && user.errorUpdatedAt === 0)
    return (
      <div className="loading">
        <Activity /> Connecting to PulseGrid…
      </div>
    );
  const signedOut = user.error instanceof ApiError && user.error.status === 401;
  return (
    <>
      {user.error && !signedOut && (
        <div role="alert" className="error">
          Connection interrupted: {message(user.error)}{" "}
          <button onClick={() => user.refetch()}>Try again</button>
        </div>
      )}
      {user.data && !signedOut ? (
        <Dashboard user={user.data} />
      ) : (
        <Login
          onLogin={() => {
            client.removeQueries({
              predicate: (query) => query.queryKey[0] !== "me",
            });
            void user.refetch();
          }}
        />
      )}
    </>
  );
}
function Login({ onLogin }: { onLogin: () => void }) {
  const [callback] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)),
  );
  const [challenge, setChallenge] = useState(
    callback.get("google_challenge") || "",
  );
  const googleErrors: Record<string, string> = {
    not_configured: "Google sign-in is not configured yet.",
    expired: "Google sign-in expired. Please try again.",
    cancelled: "Google sign-in was cancelled. You can try again.",
    failed: "Google sign-in could not be completed. Please try again.",
    email_exists:
      "Contact your administrator to enable Google sign-in for this existing account.",
  };
  const [error, setError] = useState(
    googleErrors[callback.get("google_error") || ""] || "",
  );
  const [busy, setBusy] = useState(false);
  const google = useData<{ enabled: boolean }>(
    "google-auth",
    "/auth/google/config",
  );
  useEffect(() => {
    if (callback.has("google_error") || callback.has("google_challenge"))
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
  }, [callback]);
  async function signInWithGoogle() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ authorization_url: string }>(
        "/auth/google/start",
        "POST",
      );
      window.location.assign(result.authorization_url);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      await api("/auth/verify", "POST", { challenge, code: f.get("code") });
      onLogin();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-art">
        <div className="brand">
          <span className="brand-icon">
            <Activity size={24} />
          </span>
          PulseGrid
        </div>
        <div>
          <div className="eyebrow">A CLEAR SIGNAL, EVERY TIME</div>
          <h1>
            Stay close to
            <br />
            what keeps
            <br />
            <span>you running.</span>
          </h1>
          <p>
            Your services, their health, and the moments
            <br />
            that matter. All in one place.
          </p>
        </div>
        <small>
          <span className="live-dot" /> Independent monitoring. Lasting history.
        </small>
      </div>
      <section className="login-form">
        <div className="eyebrow">YOUR MONITORING WORKSPACE</div>
        <h2>{challenge ? "One more check." : "Welcome to PulseGrid."}</h2>
        <p>
          {challenge
            ? "Enter your authenticator code or a one-time recovery code."
            : "Sign in or create an account with Google to monitor your services."}
        </p>
        {!challenge && (
          <>
            <button
              className="google-button wide"
              type="button"
              disabled={busy || !google.data?.enabled}
              onClick={signInWithGoogle}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  fill="#4285F4"
                  d="M21.6 12.23c0-.71-.06-1.39-.18-2.05H12v3.88h5.38a4.6 4.6 0 0 1-2 3.02v2.51h3.24c1.9-1.75 2.98-4.33 2.98-7.36Z"
                />
                <path
                  fill="#34A853"
                  d="M12 22c2.7 0 4.96-.9 6.62-2.41l-3.24-2.51c-.9.6-2.05.96-3.38.96-2.6 0-4.81-1.76-5.6-4.12H3.06v2.59A10 10 0 0 0 12 22Z"
                />
                <path
                  fill="#FBBC05"
                  d="M6.4 13.92a6 6 0 0 1 0-3.84V7.49H3.06a10 10 0 0 0 0 9.02l3.34-2.59Z"
                />
                <path
                  fill="#EA4335"
                  d="M12 5.96c1.47 0 2.79.51 3.83 1.51L18.7 4.6A9.6 9.6 0 0 0 12 2a10 10 0 0 0-8.94 5.49l3.34 2.59C7.19 7.72 9.4 5.96 12 5.96Z"
                />
              </svg>
              {busy ? "Please wait…" : "Continue with Google"}
            </button>
            {!google.isPending && !google.data?.enabled && (
              <p className="hint">Google sign-in is not configured yet.</p>
            )}
          </>
        )}
        {challenge && (
          <form onSubmit={submit}>
            <Field label="Authentication or recovery code">
              <input
                name="code"
                autoComplete="one-time-code"
                required
                autoFocus
              />
            </Field>
            <button className="primary wide" disabled={busy}>
              {busy ? "Please wait…" : "Verify and sign in"}
              <ArrowUpRight size={17} />
            </button>
          </form>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {challenge && (
          <button
            className="text-button"
            disabled={busy}
            onClick={() => setChallenge("")}
          >
            Start again
          </button>
        )}
      </section>
    </main>
  );
}
function Dashboard({ user }: { user: User }) {
  const client = useQueryClient();
  const router = useRouter();
  const params = useSearchParams();
  const requestedView = params.get("view");
  const view = params.get("monitor")
    ? "detail"
    : requestedView === "notifications" || requestedView === "alerts"
      ? "alerts"
      : requestedView === "settings"
        ? "settings"
        : "dashboard";
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [modal, setModal] = useState<"monitor" | null>(null);
  const [editing, setEditing] = useState<Monitor | undefined>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const data = useData<{
    monitors: Monitor[];
    monitor_total: number;
    limit: number;
  }>("dashboard", showArchived ? "/dashboard?archived=true" : "/dashboard");
  const worker = useData<{ workers: { id: string; stale: boolean }[] }>(
    "worker",
    "/worker-status",
  );
  const monitors = data.data?.monitors || [];
  const atMonitorLimit =
    !!data.data && data.data.monitor_total >= data.data.limit;
  const online = worker.data?.workers.some((w) => !w.stale);
  const problems = monitors.filter(
    (m) => m.state === "DOWN" || m.state === "SUSPECT",
  ).length;
  const unknown = monitors.filter((m) => m.state === "UNKNOWN").length;
  const paused = monitors.filter((m) => m.state === "PAUSED").length;
  async function act(url: string, method = "POST", body?: unknown) {
    setError("");
    try {
      await api(url, method, body);
      await client.invalidateQueries();
    } catch (e) {
      setError(message(e));
      throw e;
    }
  }
  const action = (url: string, method = "POST", body?: unknown) => {
    void act(url, method, body).catch(() => {});
  };
  const navigate = (v = "dashboard") =>
    router.push(`/?view=${v}`, { scroll: true });
  const priority: Record<string, number> = {
    DOWN: 0,
    SUSPECT: 1,
    UNKNOWN: 2,
    UP: 3,
    PAUSED: 4,
  };
  const shown = monitors
    .filter((m) =>
      `${m.name} ${m.url}`.toLowerCase().includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        (priority[a.state] ?? 5) - (priority[b.state] ?? 5) ||
        a.name.localeCompare(b.name),
    );
  const detail = useData<Monitor>(
    "monitor",
    `/monitors/${params.get("monitor")}`,
    !!params.get("monitor"),
  );
  const current =
    detail.data || monitors.find((m) => m.id === params.get("monitor")) || null;
  const openService = () => {
    setEditing(undefined);
    setModal("monitor");
  };
  const summary = data.isPending
    ? "Checking your services…"
    : data.error
      ? "Service status is unavailable"
      : !monitors.length
        ? "Your first service starts here"
        : worker.data && !online
          ? "Monitoring has stopped"
          : problems
            ? `${problems} service${problems === 1 ? " needs" : "s need"} attention`
            : unknown
              ? "Some services have no recent checks"
              : paused === monitors.length
                ? "All services are paused"
                : paused
                  ? "Your active services are working"
                  : "All services are working";
  return (
    <div className="app-shell simple-shell">
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span className="brand-icon">
            <Activity size={22} />
          </span>
          PulseGrid
        </Link>
        <div className="workspace">
          <div className="workspace-avatar">P</div>
          <div>
            <strong>Your workspace</strong>
            <small>Website & API monitoring</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          <button
            aria-current={
              view === "dashboard" || view === "detail" ? "page" : undefined
            }
            className={
              view === "dashboard" || view === "detail" ? "active" : ""
            }
            onClick={() => navigate()}
          >
            <LayoutDashboard size={18} />
            Dashboard
          </button>
          <button
            aria-current={view === "alerts" ? "page" : undefined}
            className={view === "alerts" ? "active" : ""}
            onClick={() => navigate("alerts")}
          >
            <Bell size={18} />
            Alerts
          </button>
          <button
            aria-current={view === "settings" ? "page" : undefined}
            className={view === "settings" ? "active" : ""}
            onClick={() => navigate("settings")}
          >
            <Settings size={18} />
            Settings
          </button>
        </nav>
        <div className="sidebar-bottom">
          <button onClick={() => navigate("settings")} className="profile">
            <span className="avatar">{user.email[0].toUpperCase()}</span>
            <span className="truncate">
              {user.email}
              <small>Your account</small>
            </span>
            <ChevronRight size={16} />
          </button>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={14} />
            <strong>
              {view === "detail"
                ? current?.name || "Service"
                : view === "alerts"
                  ? "Alerts"
                  : view === "settings"
                    ? "Settings"
                    : "Dashboard"}
            </strong>
          </span>
          <span className="poll-label">Updates automatically</span>
        </header>
        <div className="content">
          {(error || data.error || worker.error) && (
            <div className="alert" role="alert">
              <span>{error || message(data.error || worker.error)}</span>
              <button
                onClick={() => {
                  setError("");
                  void client.invalidateQueries();
                }}
              >
                Retry
              </button>
            </div>
          )}
          {!!monitors.length &&
            worker.data &&
            !online &&
            view !== "dashboard" && (
              <div className="alert" role="alert">
                Monitoring has stopped. Service status may be out of date.
              </div>
            )}
          {notice && (
            <div className="notice" role="status">
              {notice}
              <button
                className="icon"
                aria-label="Dismiss notice"
                onClick={() => setNotice("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {view === "dashboard" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">A LITTLE PEACE OF MIND</div>
                  <h1>
                    Dashboard<span className="heading-dot">.</span>
                  </h1>
                  <p>Your services and anything that needs your attention.</p>
                </div>
                <button onClick={() => setShowArchived(!showArchived)}>
                  {showArchived
                    ? "Show active services"
                    : "Show archived services"}
                </button>
                <button
                  className="primary"
                  onClick={openService}
                  disabled={atMonitorLimit}
                >
                  <Plus size={17} />
                  Add service
                </button>
              </div>
              {atMonitorLimit && (
                <p className="notice" role="status">
                  You’ve reached the {data.data?.limit}-service limit. Paused
                  and archived services count toward the limit. Delete a service
                  to add another.
                </p>
              )}
              <div
                className={`status-banner ${problems || (monitors.length && worker.data && !online) ? "needs-attention" : ""}`}
                role="status"
              >
                <span
                  className={`status-orb ${problems ? "down" : unknown || !online || !monitors.length || paused === monitors.length ? "unknown" : ""}`}
                >
                  <Activity size={25} />
                </span>
                <div>
                  <strong>{summary}</strong>
                  <p>
                    {data.isPending || data.error
                      ? "We’ll update this page when checks are available."
                      : !monitors.length
                        ? "Add a website or API. We’ll take care of the checks."
                        : worker.data && !online
                          ? "Checks are not running. Contact your server operator to restore monitoring."
                          : `${monitors.filter((m) => m.state === "UP").length} working · ${problems} need attention · ${unknown} without recent checks · ${paused} paused`}
                  </p>
                </div>
              </div>
              <section
                className="panel service-panel"
                aria-label="Your services"
              >
                <div className="panel-toolbar service-toolbar">
                  <h2>
                    Your services
                    <span className="muted">{shown.length}</span>
                  </h2>
                  <div className="toolbar-actions">
                    <label className="search">
                      <Search size={16} />
                      <input
                        aria-label="Search services"
                        placeholder="Find a service…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </label>
                  </div>
                </div>
                {data.isPending ? (
                  <Empty>Loading services…</Empty>
                ) : data.error ? (
                  <Empty>
                    We couldn’t load your services. Please try again above.
                  </Empty>
                ) : !shown.length ? (
                  <div className="service-empty">
                    <Radio size={32} />
                    <h2>
                      {search
                        ? "No services in this view"
                        : "Keep an eye on what matters"}
                    </h2>
                    <p>
                      {search
                        ? "Try another search."
                        : "Paste your website or API URL to start."}
                    </p>
                    {!search && !atMonitorLimit && (
                      <button className="primary" onClick={openService}>
                        <Plus size={16} />
                        Add your first service
                      </button>
                    )}
                  </div>
                ) : (
                  <ul className="service-list">
                    {shown.map((m) => (
                      <li className="service-row" key={m.id}>
                        <button
                          className="monitor-link"
                          onClick={() =>
                            router.push(`/?monitor=${m.id}`, { scroll: true })
                          }
                        >
                          <span
                            className={`endpoint-icon ${m.state.toLowerCase()}`}
                          >
                            <Radio size={18} />
                          </span>
                          <span>
                            <strong>{m.name}</strong>
                            <small>{m.url}</small>
                          </span>
                        </button>
                        <Badge state={m.state} />
                        <span className="last-check">
                          {m.last_observed
                            ? `Checked ${Math.max(0, Math.floor((data.dataUpdatedAt - new Date(m.last_observed).getTime()) / 1000))}s ago`
                            : "Waiting for first check"}
                        </span>
                        <div className="service-actions">
                          <button
                            className="icon"
                            disabled={!!m.archived_at}
                            aria-label={`${m.state === "PAUSED" ? "Resume" : "Pause"} ${m.name}`}
                            onClick={() =>
                              action(
                                `/monitors/${m.id}/${m.state === "PAUSED" ? "resume" : "pause"}`,
                              )
                            }
                          >
                            {m.state === "PAUSED" ? (
                              <Play size={16} />
                            ) : (
                              <Pause size={16} />
                            )}
                          </button>
                          <button
                            className="icon"
                            aria-label={`Edit ${m.name}`}
                            onClick={() => {
                              setEditing(m);
                              setModal("monitor");
                            }}
                          >
                            <Settings size={16} />
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <p className="hint">
                <CircleHelp size={14} />
                “No recent checks” means we can’t confirm the service’s status.
                It doesn’t mean it’s down.
              </p>
            </>
          )}
          {view === "detail" && !current && (
            <Empty>
              {detail.isPending
                ? "Loading service…"
                : "Service unavailable or not found."}
            </Empty>
          )}
          {view === "detail" && current && (
            <MonitorDetail
              key={current.id}
              monitor={current}
              back={() => navigate()}
              action={action}
              edit={() => {
                setEditing(current);
                setModal("monitor");
              }}
              remove={() => {
                if (
                  confirm(
                    "Permanently delete this service and all its check and outage history?",
                  )
                ) {
                  void act(
                    `/monitors/${current.id}?confirm_history=true`,
                    "DELETE",
                  )
                    .then(() => navigate())
                    .catch(() => {});
                }
              }}
            />
          )}
          {view === "alerts" && <Notifications act={act} user={user} />}
          {view === "settings" && (
            <Account user={user} act={act} notice={setNotice} />
          )}
        </div>
      </main>
      {modal === "monitor" && (
        <MonitorForm monitor={editing} act={act} close={() => setModal(null)} />
      )}
    </div>
  );
}
function MonitorForm({
  monitor,
  act,
  close,
}: {
  monitor?: Monitor;
  act: (u: string, m?: string, b?: unknown) => Promise<void>;
  close: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      await act(
        monitor ? `/monitors/${monitor.id}` : "/monitors",
        monitor ? "PUT" : "POST",
        {
          url: f.get("url"),
          name: String(f.get("name") || "").trim(),
          method: f.get("method") || "GET",
          interval: Number(f.get("interval")),
          timeout: Number(f.get("timeout")),
          statuses: String(f.get("statuses")).split(",").map(Number),
        },
      );
      close();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={monitor ? "Edit service" : "Add service"} close={close}>
      <form onSubmit={submit}>
        <Field label="Website or API URL">
          <input
            name="url"
            type="url"
            defaultValue={monitor?.url}
            placeholder="https://example.com"
            required
            autoFocus
          />
        </Field>
        <Field label="Name (optional)">
          <input
            name="name"
            defaultValue={monitor?.name}
            placeholder="We’ll use the website name"
            maxLength={100}
          />
        </Field>
        <p className="hint">
          Checks run automatically. You can adjust how we check in Advanced
          settings.
        </p>
        <details className="advanced">
          <summary>Advanced settings</summary>
          <div className="advanced-body">
            <div className="form-row">
              <Field label="Check frequency (seconds)">
                <input
                  type="number"
                  name="interval"
                  min={30}
                  max={86400}
                  defaultValue={monitor?.interval || 30}
                  required
                />
              </Field>
              <Field label="Wait for a response (seconds)">
                <input
                  type="number"
                  name="timeout"
                  min={1}
                  max={20}
                  defaultValue={monitor?.timeout || 10}
                  required
                />
              </Field>
            </div>
            <div className="form-row">
              <Field label="HTTP method">
                <select name="method" defaultValue={monitor?.method || "GET"}>
                  <option>GET</option>
                  <option>HEAD</option>
                </select>
              </Field>
              <Field label="Successful response codes">
                <input
                  name="statuses"
                  defaultValue={monitor?.statuses.join(",") || "200"}
                  pattern="[0-9, ]+"
                  required
                />
              </Field>
            </div>
            <p className="hint">
              Redirects are not followed. Internal addresses need permission
              from your server operator.
            </p>
          </div>
        </details>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary wide" disabled={busy}>
          {busy ? "Saving…" : monitor ? "Save changes" : "Start monitoring"}
        </button>
      </form>
    </Modal>
  );
}
const CHECKS_PER_PAGE = 10;

function MonitorDetail({
  monitor: m,
  back,
  action,
  edit,
  remove,
}: {
  monitor: Monitor;
  back: () => void;
  action: (u: string, m?: string, b?: unknown) => void;
  edit: () => void;
  remove: () => void;
}) {
  const [hours, setHours] = useState(24);
  const [checkPage, setCheckPage] = useState(0);
  const [incidentOffset, setIncidentOffset] = useState(0);
  const checks = useData<Result[]>(
    "results",
    `/monitors/${m.id}/results?hours=${hours}&limit=${CHECKS_PER_PAGE + 1}&offset=${checkPage * CHECKS_PER_PAGE}`,
  );
  const metrics = useData<Metrics>(
    "metrics",
    `/monitors/${m.id}/metrics?hours=${hours}`,
  );
  const incidents = useData<Incident[]>(
    "incidents",
    `/monitors/${m.id}/incidents?offset=${incidentOffset}`,
  );
  const versions = useData<
    {
      id: string;
      starts_at: string;
      ends_at: string | null;
      active: boolean;
      settings_known: boolean;
      url: string | null;
      method: string | null;
      interval: number;
    }[]
  >("versions", `/monitors/${m.id}/versions`);
  const stats = metrics.data;
  const windowEnd = stats ? Date.parse(stats.window_end) : 0;
  const trend = stats?.latency_trend.map((bucket) => ({
    time: Date.parse(bucket.starts_at),
    latency: bucket.median_ms,
  }));
  const rows = checks.data?.slice(0, CHECKS_PER_PAGE) ?? [];
  const hasMoreChecks = (checks.data?.length ?? 0) > CHECKS_PER_PAGE;
  return (
    <>
      <button className="text-button service-back" onClick={back}>
        <ArrowLeft size={15} /> Back to dashboard
      </button>
      <div className="page-heading service-heading">
        <div>
          <div className="eyebrow">SERVICE STATUS</div>
          <h1>{m.name}</h1>
          <p>{m.url}</p>
        </div>
        <div className="button-group">
          <div className="service-status-controls">
            <Badge state={m.state} />
            <button
              disabled={!!m.archived_at}
              onClick={() =>
                action(
                  `/monitors/${m.id}/${m.state === "PAUSED" ? "resume" : "pause"}`,
                )
              }
            >
              {m.state === "PAUSED" ? <Play size={15} /> : <Pause size={15} />}{" "}
              {m.state === "PAUSED" ? "Resume" : "Pause"}
            </button>
          </div>
          {!m.archived_at && <button onClick={edit}>Edit service</button>}
          <button
            onClick={() =>
              action(
                `/monitors/${m.id}/${m.archived_at ? "restore" : "archive"}`,
              )
            }
          >
            {m.archived_at ? "Restore service" : "Archive service"}
          </button>
          <button className="danger" onClick={remove}>
            Delete
          </button>
        </div>
      </div>
      <p className="hint">
        {m.archived_at ? "Archived · history retained" : "Monitoring enabled"} ·
        Last checked: {stamp(m.last_observed)} · Check frequency: {m.interval}s
      </p>
      {(versions.data?.length || 0) > 1 && (
        <p className="hint">
          History includes checks made with earlier settings. See Configuration
          history in More details.
        </p>
      )}
      <div className="section-heading service-history-heading">
        <h2>Service history</h2>
        <div className="metric-window">
          <select
            aria-label="Metric window"
            value={hours}
            onChange={(e) => {
              setHours(Number(e.target.value));
              setCheckPage(0);
            }}
          >
            <option value={1}>Last hour</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
            <option value={720}>Last 30 days</option>
          </select>
          <ChevronDown size={16} aria-hidden="true" />
        </div>
      </div>
      {(metrics.error || incidents.error) && (
        <p className="error" role="alert">
          {message(metrics.error || incidents.error)}
        </p>
      )}
      <div className="stat-grid simple-metrics">
        <section className="stat">
          <div>UPTIME</div>
          <strong className="metric-value">{pct(stats?.uptime)}</strong>
          <small>Successful checks in the selected period</small>
        </section>
        <section className="stat">
          <div>TYPICAL RESPONSE TIME</div>
          <strong className="metric-value">{ms(stats?.median_ms)}</strong>
          <small>Median time until a response starts</small>
        </section>
      </div>
      <section className="panel chart-panel">
        <h2>Response time</h2>
        <p className="hint">
          Median time to response headers for successful checks
          {stats &&
            ` · per ${
              stats.latency_bucket_seconds >= 3600
                ? `${stats.latency_bucket_seconds / 3600} hr`
                : `${stats.latency_bucket_seconds / 60} min`
            }`}
        </p>
        {metrics.isPending ? (
          <Empty>Loading response times…</Empty>
        ) : metrics.error ? (
          <Empty>Response times are unavailable.</Empty>
        ) : !trend?.some((point) => point.latency != null) ? (
          <Empty>
            No successful checks with response times in this period.
          </Empty>
        ) : (
          <div className="chart">
            <ResponsiveContainer
              width="100%"
              height="100%"
              initialDimension={{ width: 400, height: 230 }}
              minWidth={0}
            >
              <AreaChart data={trend}>
                <defs>
                  <linearGradient id="latency" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#718554" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#718554" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  stroke="#eceee7"
                />
                <XAxis
                  dataKey="time"
                  type="number"
                  scale="time"
                  domain={[windowEnd - hours * 3600_000, windowEnd]}
                  tickFormatter={(value: number) =>
                    hours > 24
                      ? new Date(value).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                        })
                      : new Date(value).toLocaleTimeString(undefined, {
                          hour: "numeric",
                          minute: "2-digit",
                        })
                  }
                  tick={{ fontSize: 11 }}
                  minTickGap={24}
                />
                <YAxis tick={{ fontSize: 11 }} unit="ms" />
                <Tooltip
                  labelFormatter={(value) =>
                    new Date(Number(value)).toLocaleString()
                  }
                  formatter={(value) => ms(Number(value))}
                />
                <Area
                  isAnimationActive={false}
                  dataKey="latency"
                  name="Median response time"
                  stroke="#667d46"
                  fill="url(#latency)"
                  connectNulls={false}
                  dot={{ r: 1.5, fill: "#667d46", strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>
      {stats?.retention_clipped && (
        <p className="notice" role="status">
          The selected window is limited to retained checks, starting{" "}
          {stamp(stats.window_start)}. Purged history is excluded from coverage.
        </p>
      )}
      <section className="panel">
        <div className="panel-toolbar">
          <h2>Recent outages</h2>
          <span className="muted">Confirmed after two failed checks</span>
        </div>
        {!incidents.data?.length ? (
          <Empty>
            {incidents.isPending
              ? "Loading outages…"
              : incidents.error
                ? "Outage history is unavailable."
                : "No outages recorded on this page."}
          </Empty>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>STATUS</th>
                  <th>FIRST DETECTED</th>
                  <th>RECOVERED</th>
                </tr>
              </thead>
              <tbody>
                {incidents.data.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <Badge
                        state={i.closed_at ? "UP" : "DOWN"}
                        label={i.closed_at ? "Recovered" : "Down"}
                      />
                    </td>
                    <td>{stamp(i.opened_at)}</td>
                    <td>{i.closed_at ? stamp(i.closed_at) : "Still down"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <button
            disabled={!incidentOffset}
            onClick={() => setIncidentOffset(Math.max(0, incidentOffset - 100))}
          >
            Previous
          </button>
          <button
            disabled={(incidents.data?.length || 0) < 100}
            onClick={() => setIncidentOffset(incidentOffset + 100)}
          >
            Next
          </button>
        </div>
      </section>
      <details className="advanced service-more">
        <summary>More details</summary>
        <div className="advanced-body">
          {" "}
          <div className="stat-grid">
            {[
              [
                "CHECK-BASED UPTIME",
                pct(stats?.uptime),
                `${stats?.successful || 0} / ${stats?.completed || 0} completed checks`,
              ],
              [
                "COVERAGE",
                pct(stats?.coverage),
                `${stats?.missing || 0} missing · ${stats?.expected || 0} expected`,
              ],
              [
                "MEDIAN LATENCY",
                ms(stats?.median_ms),
                "Successful checks · time to headers",
              ],
              [
                "P95 / P99",
                `${ms(stats?.p95_ms)} / ${ms(stats?.p99_ms)}`,
                `${stats?.failed || 0} failed or timed-out checks`,
              ],
            ].map(([label, value, sub]) => (
              <section className="stat" key={label}>
                <div>{label}</div>
                <strong className="metric-value">{value}</strong>
                <small>{sub}</small>
              </section>
            ))}
          </div>
          <section className="panel chart-panel">
            <h2>Latency distribution</h2>
            <p className="hint">All successful checks in the selected window</p>
            <div className="chart">
              <ResponsiveContainer
                width="100%"
                height="100%"
                initialDimension={{ width: 400, height: 230 }}
                minWidth={0}
              >
                <BarChart data={stats?.histogram || []}>
                  <CartesianGrid vertical={false} stroke="#eceee7" />
                  <XAxis dataKey="range" tick={{ fontSize: 10 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar
                    isAnimationActive={false}
                    dataKey="count"
                    fill="#879b69"
                    radius={[4, 4, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
          <section className="panel">
            <div className="history-panel-heading">
              <h2>Configuration history</h2>
              <p className="hint">
                Latest 100 changes. Earlier settings that were never recorded
                are shown as unknown.
              </p>
              {versions.error && (
                <p className="error">Configuration history is unavailable.</p>
              )}
            </div>
            {!versions.error && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>FROM</th>
                      <th>UNTIL</th>
                      <th>STATE</th>
                      <th>URL</th>
                      <th>CHECK</th>
                    </tr>
                  </thead>
                  <tbody>
                    {versions.data?.map((v) => (
                      <tr key={v.id}>
                        <td>{stamp(v.starts_at)}</td>
                        <td>{v.ends_at ? stamp(v.ends_at) : "Current"}</td>
                        <td>{v.active ? "Active" : "Paused / archived"}</td>
                        <td>
                          {v.settings_known
                            ? v.url
                            : "Earlier URL not recorded"}
                        </td>
                        <td>
                          {v.method || "Unknown method"} · {v.interval}s
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <div className="definitions">
            <strong>How these numbers work</strong>
            <p>
              Uptime is successful completed checks / all completed checks.
              Coverage compares received checks to expected checks while active.
              Expected checks use active seconds / interval, rounded down;
              coverage caps at 100% for window boundaries. Missing checks are
              separate from failures. Latency percentiles include only
              successful timed checks. Detection times reflect sampling, not the
              exact start of a target outage.
            </p>
          </div>
          <section className="panel">
            <div className="panel-toolbar">
              <h2>Recent checks</h2>
              <span className="muted">Newest first</span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>OBSERVED</th>
                    <th>RESULT</th>
                    <th>HTTP</th>
                    <th>TIME TO HEADERS</th>
                    <th>DETAIL</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{stamp(r.observed_at)}</td>
                      <td>
                        <Badge state={r.success ? "UP" : "DOWN"} />
                      </td>
                      <td>{r.status_code || "—"}</td>
                      <td>{ms(r.latency_ms)}</td>
                      <td className="muted">
                        {r.error || "Accepted response"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {checks.isPending ? (
              <Empty>Loading checks…</Empty>
            ) : checks.error ? (
              <Empty>Recent checks are unavailable.</Empty>
            ) : !rows.length ? (
              <Empty>No completed checks on this page.</Empty>
            ) : null}
            <nav className="pagination" aria-label="Recent checks pagination">
              <button
                disabled={checkPage === 0}
                onClick={() => setCheckPage((page) => Math.max(0, page - 1))}
              >
                Previous
              </button>
              <span className="muted" aria-live="polite">
                Page {checkPage + 1}
              </span>
              <button
                disabled={!hasMoreChecks || !!checks.error}
                onClick={() => setCheckPage((page) => page + 1)}
              >
                Next
              </button>
            </nav>
          </section>
        </div>
      </details>
    </>
  );
}
function EmailAlerts({
  user,
  channels,
  loading,
  act,
}: {
  user: User;
  channels: Channel[];
  loading: boolean;
  act: (u: string, m?: string, b?: unknown) => Promise<void>;
}) {
  const settings = useData<{ email_available: boolean }>(
    "notification-settings",
    "/notification-settings",
  );
  const channel = channels.find((c) => c.kind === "email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function testEmail() {
    if (!channel) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await act(`/notification-channels/${channel.id}/test`);
      setNotice("Test email queued. Follow its status under Delivery status.");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel email-alerts">
      <div className="email-heading">
        <span className="folder-icon">
          <Bell size={22} />
        </span>
        <div>
          <h2>Email alerts for your account</h2>
          <p className="muted">
            Alerts go to the email you signed up with when a service goes down
            or recovers.
          </p>
        </div>
      </div>
      {loading ? (
        <p className="hint">Loading your alert settings…</p>
      ) : (
        <form
          key={`${channel?.id || "new"}:${channel?.email || ""}:${channel?.enabled}`}
          onSubmit={async (e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            setBusy(true);
            setError("");
            setNotice("");
            try {
              await act(
                channel
                  ? `/notification-channels/${channel.id}`
                  : "/notification-channels",
                channel ? "PUT" : "POST",
                {
                  name: channel?.name || "Email alerts",
                  kind: "email",
                  enabled: f.get("enabled") === "on",
                },
              );
              setNotice(
                "Email alert settings saved. They cover all your current and future services.",
              );
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Your account email">
            <input type="email" value={user.email} readOnly />
          </Field>
          <label className="checkbox">
            <input
              type="checkbox"
              name="enabled"
              defaultChecked={channel?.enabled ?? true}
            />
            Email me about outages and recoveries
          </label>
          <p className="hint">
            Applies to every service, including ones you add later.
          </p>
          <div className="button-group">
            <button className="primary" disabled={busy}>
              {" "}
              {busy ? "Saving…" : "Save email alerts"}
            </button>
            <button
              type="button"
              disabled={
                busy || !channel?.enabled || !settings.data?.email_available
              }
              onClick={testEmail}
            >
              Send test email
            </button>
          </div>
        </form>
      )}
      {settings.data && !settings.data.email_available && (
        <p className="notice" role="status">
          Email delivery hasn’t been set up on this server yet. You can save
          your alert preferences now, but messages won’t send until setup is
          complete.
        </p>
      )}
      {(error || settings.error) && (
        <p className="error" role="alert">
          {error || "Email delivery settings are temporarily unavailable."}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
function deliveryStatus(d: Delivery) {
  if (d.delivered_at) return "Delivered";
  if (d.exhausted) return "Failed · retries exhausted";
  if (d.last_error) return "Failed · retry scheduled";
  return "Queued";
}

const FAILED_DELIVERIES_PER_PAGE = 10;

function Notifications({
  act,
  user,
}: {
  user: User;
  act: (u: string, m?: string, b?: unknown) => Promise<void>;
}) {
  const channels = useData<Channel[]>("channels", "/notification-channels");
  const [advanced, setAdvanced] = useState(false);
  const [offset, setOffset] = useState(0);
  const [failedOffset, setFailedOffset] = useState(0);
  const tests = useData<Delivery[]>(
    "deliveries",
    "/deliveries?event_kind=test&limit=1",
  );
  const failed = useData<Delivery[]>(
    "deliveries",
    `/deliveries?failed=true&limit=${FAILED_DELIVERIES_PER_PAGE + 1}&offset=${failedOffset}`,
  );
  const latestTest = tests.data?.[0];
  const deliveries = useData<Delivery[]>(
    "deliveries",
    `/deliveries?offset=${offset}`,
    advanced,
  );
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState<Channel | null>(null);
  const [channelKind, setChannelKind] = useState<"webhook" | "email">(
    "webhook",
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const run = (u: string, m = "POST", b?: unknown) => {
    void act(u, m, b)
      .then(() => setNotice("Saved. Delivery results update below."))
      .catch((e) => setError(message(e)));
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WHEN SOMETHING CHANGES</div>
          <h1>
            Alerts<span className="heading-dot">.</span>
          </h1>
          <p>Outages and recoveries, delivered to you.</p>
        </div>
      </div>
      {(error || channels.error || deliveries.error) && (
        <p className="error" role="alert">
          {error || message(channels.error || deliveries.error)}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <EmailAlerts
        user={user}
        channels={channels.data || []}
        loading={channels.isPending || !!channels.error}
        act={act}
      />
      <section className="panel" aria-label="Delivery status">
        <div className="panel-toolbar">
          <h2>Delivery status</h2>
        </div>
        <div className="delivery-test" role="status">
          <strong>Latest test:</strong>{" "}
          {tests.error ? (
            "Test status is unavailable."
          ) : tests.isPending ? (
            "Loading…"
          ) : latestTest ? (
            <>
              <span
                className={
                  latestTest.delivered_at
                    ? "success"
                    : latestTest.last_error
                      ? "error"
                      : ""
                }
              >
                {deliveryStatus(latestTest)}
              </span>
              {" · "}
              {channels.data?.find((c) => c.id === latestTest.channel_id)
                ?.name || "Alert channel"}
              {latestTest.test_queued_at &&
                ` · Queued ${stamp(latestTest.test_queued_at)}`}
              {latestTest.delivered_at &&
                ` · Delivered ${stamp(latestTest.delivered_at)}`}
              {latestTest.last_error && (
                <p className="muted">{latestTest.last_error}</p>
              )}
            </>
          ) : (
            "No tests sent yet."
          )}
        </div>
        <div className="panel-toolbar">
          <h2>Failed deliveries</h2>
        </div>
        {failed.error ? (
          <Empty>Failed deliveries are unavailable.</Empty>
        ) : failed.isPending ? (
          <Empty>Loading failed deliveries…</Empty>
        ) : !failed.data?.length ? (
          <Empty>
            {failedOffset
              ? "No more failed deliveries."
              : "No unresolved delivery failures."}
          </Empty>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>CHANNEL</th>
                  <th>EVENT</th>
                  <th>STATUS</th>
                  <th>DETAIL</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {failed.data.slice(0, FAILED_DELIVERIES_PER_PAGE).map((d) => (
                  <tr key={d.id}>
                    <td>
                      {channels.data?.find((c) => c.id === d.channel_id)
                        ?.name || "Alert channel"}
                    </td>
                    <td>{d.event_kind}</td>
                    <td className="error">{deliveryStatus(d)}</td>
                    <td>
                      {d.last_error}
                      {!d.exhausted && (
                        <small className="muted block">
                          Next attempt: {stamp(d.next_attempt)}
                        </small>
                      )}
                    </td>
                    <td>
                      <button onClick={() => run(`/deliveries/${d.id}/retry`)}>
                        Retry
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(failedOffset > 0 ||
          (failed.data?.length ?? 0) > FAILED_DELIVERIES_PER_PAGE) && (
          <nav className="pagination" aria-label="Failed deliveries pagination">
            <button
              disabled={!failedOffset}
              onClick={() =>
                setFailedOffset(failedOffset - FAILED_DELIVERIES_PER_PAGE)
              }
            >
              Previous
            </button>
            <button
              disabled={
                (failed.data?.length ?? 0) <= FAILED_DELIVERIES_PER_PAGE
              }
              onClick={() =>
                setFailedOffset(failedOffset + FAILED_DELIVERIES_PER_PAGE)
              }
            >
              Next
            </button>
          </nav>
        )}
      </section>
      <details
        className="advanced alert-advanced"
        onToggle={(e) => setAdvanced(e.currentTarget.open)}
      >
        <summary>Advanced alert settings</summary>
        <div className="advanced-body">
          <div className="section-heading">
            <h2>Extra alert destinations</h2>{" "}
            <button
              className="primary"
              onClick={() => {
                setEditing(null);
                setChannelKind("webhook");
                setModal(true);
              }}
            >
              <Plus size={16} />
              Add channel
            </button>
          </div>
          <div className="definitions">
            <strong>At-least-once delivery</strong>
            <p>
              Incident events and notification intents commit together. Delivery
              retries up to eight times with exponential backoff. A provider may
              receive an event more than once after a crash or ambiguous
              response. Webhooks include a stable event_id and Idempotency-Key
              for receiver deduplication. SMTP is configured by the server
              operator.
            </p>
          </div>
          <div className="bucket-grid">
            {channels.data?.map((c) => (
              <section className="panel channel-card" key={c.id}>
                <Bell size={21} />
                <h2>{c.name}</h2>
                <p className="muted">
                  {c.kind.toUpperCase()} · {c.enabled ? "Enabled" : "Disabled"}
                </p>
                <div className="button-group">
                  <button
                    disabled={!c.enabled}
                    onClick={() => run(`/notification-channels/${c.id}/test`)}
                  >
                    Send test
                  </button>
                  <button
                    onClick={() => {
                      setEditing(c);
                      setChannelKind(c.kind === "email" ? "email" : "webhook");
                      setModal(true);
                    }}
                  >
                    Edit
                  </button>
                  <button
                    className="danger"
                    onClick={() => {
                      if (
                        confirm("Delete this channel and its delivery history?")
                      )
                        run(`/notification-channels/${c.id}`, "DELETE");
                    }}
                  >
                    Delete
                  </button>
                </div>
                <p className="hint">
                  Alerts for all current and future services
                </p>
              </section>
            ))}
          </div>
          {!channels.data?.length && (
            <Empty>
              Add a webhook or another email destination for your services.
            </Empty>
          )}
          <section className="panel">
            <div className="panel-toolbar">
              <h2>Delivery history</h2>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>CHANNEL</th>
                    <th>EVENT</th>
                    <th>ATTEMPTS</th>
                    <th>DELIVERY</th>
                    <th>DETAIL</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {deliveries.data?.map((d) => (
                    <tr key={d.id}>
                      <td>
                        {
                          channels.data?.find((c) => c.id === d.channel_id)
                            ?.name
                        }
                      </td>
                      <td>
                        {d.event_kind}
                        <small className="muted block">
                          {d.id.slice(0, 8)}
                        </small>
                      </td>
                      <td>{d.attempts}</td>
                      <td>
                        {d.delivered_at ? (
                          <span className="success">Delivered</span>
                        ) : d.exhausted ? (
                          <span className="error">Exhausted</span>
                        ) : (
                          "Pending"
                        )}
                      </td>
                      <td className="muted">
                        {d.delivered_at
                          ? stamp(d.delivered_at)
                          : d.last_error || `Next: ${stamp(d.next_attempt)}`}
                      </td>
                      <td>
                        {!d.delivered_at && (
                          <button
                            onClick={() => run(`/deliveries/${d.id}/retry`)}
                          >
                            Retry
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                Previous
              </button>
              <button
                disabled={(deliveries.data?.length || 0) < 100}
                onClick={() => setOffset(offset + 100)}
              >
                Next
              </button>
            </div>
          </section>
        </div>
      </details>
      {modal && (
        <Modal
          title={editing ? "Edit channel" : "Add notification channel"}
          close={() => setModal(false)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              try {
                await act(
                  editing
                    ? `/notification-channels/${editing.id}`
                    : "/notification-channels",
                  editing ? "PUT" : "POST",
                  {
                    name: f.get("name"),
                    kind: f.get("kind"),
                    destination:
                      channelKind === "webhook"
                        ? f.get("destination")
                        : undefined,
                    enabled: f.get("enabled") === "on",
                  },
                );
                setModal(false);
              } catch (e) {
                setError(message(e));
              }
            }}
          >
            <Field label="Name">
              <input name="name" defaultValue={editing?.name} required />
            </Field>
            <Field label="Type">
              <select
                name="kind"
                value={channelKind}
                onChange={(e) =>
                  setChannelKind(e.target.value as "webhook" | "email")
                }
              >
                <option value="webhook">Webhook</option>
                <option value="email">Email</option>
              </select>
            </Field>
            {channelKind === "email" ? (
              <Field label="Account email">
                <input type="email" value={user.email} readOnly />
              </Field>
            ) : (
              <Field label="Webhook URL">
                <input
                  type="url"
                  name="destination"
                  placeholder={
                    editing
                      ? "Enter destination again to update"
                      : "https://hooks.example.com/pulse"
                  }
                  required
                />
              </Field>
            )}
            <label className="checkbox">
              <input
                type="checkbox"
                name="enabled"
                defaultChecked={editing?.enabled ?? true}
              />
              Enabled
            </label>
            <p className="hint">
              Destinations are encrypted at rest. Webhook destinations follow
              the same network policy as probes.
            </p>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button className="primary wide">Save channel</button>
          </form>
        </Modal>
      )}
    </>
  );
}
function Account({
  user,
  act,
  notice,
}: {
  user: User;
  act: (u: string, m?: string, b?: unknown) => Promise<void>;
  notice: (s: string) => void;
}) {
  const client = useQueryClient();
  const sessions = useData<BrowserSession[]>("sessions", "/sessions");
  const currentSession = sessions.data?.find((session) => session.current);
  const otherSessions = sessions.data?.filter((session) => !session.current);
  const [error, setError] = useState("");
  const [endingSession, setEndingSession] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const signOutEverywhere = useRef<HTMLButtonElement>(null);
  const [enrollment, setEnrollment] = useState<{
    secret: string;
    uri: string;
    qr: string;
  } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [manage, setManage] = useState<"disable" | "recovery-codes" | null>(
    null,
  );
  const closeSignOut = () => {
    setConfirmSignOut(false);
    signOutEverywhere.current?.focus();
  };
  async function endSession(target: "current" | "all" | BrowserSession) {
    setError("");
    setEndingSession(true);
    try {
      if (typeof target === "object") {
        await act(`/sessions/${target.id}`, "DELETE");
        notice("Session ended.");
      } else {
        await act(target === "all" ? "/auth/logout-all" : "/auth/logout");
        client.clear();
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setEndingSession(false);
    }
  }
  return (
    <div className="account-settings">
      <header className="account-heading">
        <h1>Account &amp; security</h1>
        <p>
          {user.password_enabled
            ? user.email
            : "Manage your sign-in and active sessions."}
        </p>
      </header>
      {(error || sessions.error) && (
        <p className="error" role="alert">
          {error || message(sessions.error)}
        </p>
      )}
      {!user.password_enabled && (
        <section className="panel account-card">
          <div className="account-card-info">
            <span className="account-icon">
              <ShieldCheck size={20} aria-hidden="true" />
            </span>
            <div>
              <h2>Google account</h2>
              <p className="account-email">{user.email}</p>
              <p className="account-description">
                Manage your password and two-step verification in Google.
              </p>
            </div>
          </div>
          <a
            className="account-link"
            href="https://myaccount.google.com/security"
            target="_blank"
            rel="noopener noreferrer"
          >
            Manage Google security <ArrowUpRight size={15} aria-hidden="true" />
          </a>
        </section>
      )}
      {user.password_enabled && (
        <div className="settings-grid">
          <section className="panel settings-panel">
            <ShieldCheck size={26} />
            <h2>Two-factor authentication</h2>
            <p>
              {user.mfa_enabled
                ? "An authenticator or recovery code is required when you sign in."
                : "Add an authenticator to protect your account."}
            </p>
            <Badge
              state={user.mfa_enabled ? "UP" : "UNKNOWN"}
              label={user.mfa_enabled ? "Enabled" : "Not set up"}
            />
            {!user.mfa_enabled && !enrollment && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    const password = new FormData(e.currentTarget).get(
                      "password",
                    );
                    const r = await api<{ secret: string; uri: string }>(
                      "/auth/mfa/enroll",
                      "POST",
                      { password },
                    );
                    setEnrollment({
                      ...r,
                      qr: await QRCode.toDataURL(r.uri),
                    });
                  } catch (e) {
                    setError(message(e));
                  }
                }}
              >
                <Field label="Current password">
                  <input
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                  />
                </Field>
                <button className="primary">Set up authenticator</button>
              </form>
            )}
            {enrollment && (
              <>
                <p>
                  Scan this QR code with your authenticator, or enter the secret
                  manually.
                </p>
                <Image
                  unoptimized
                  className="qr"
                  src={enrollment.qr}
                  width={180}
                  height={180}
                  alt="Authenticator enrollment QR code"
                />
                <code className="secret">{enrollment.secret}</code>
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    try {
                      const code = new FormData(e.currentTarget).get("code");
                      const r = await api<{ recovery_codes: string[] }>(
                        "/auth/mfa/confirm",
                        "POST",
                        { code },
                      );
                      setCodes(r.recovery_codes);
                      setEnrollment(null);
                      await client.invalidateQueries();
                    } catch (e) {
                      setError(message(e));
                    }
                  }}
                >
                  <Field label="Six-digit authenticator code">
                    <input
                      name="code"
                      inputMode="numeric"
                      pattern="[0-9]{6}"
                      autoComplete="one-time-code"
                      required
                    />
                  </Field>
                  <button className="primary">Confirm enrollment</button>
                </form>
              </>
            )}
            {user.mfa_enabled && (
              <div className="button-group">
                <button onClick={() => setManage("recovery-codes")}>
                  Replace recovery codes
                </button>
                <button className="danger" onClick={() => setManage("disable")}>
                  Disable MFA
                </button>
              </div>
            )}
          </section>
          <section className="panel settings-panel">
            <h2>Change password</h2>
            <p>
              Changing your password revokes all sessions, including this one.
            </p>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await act(
                    "/auth/password",
                    "POST",
                    Object.fromEntries(new FormData(e.currentTarget)),
                  );
                  client.clear();
                } catch (e) {
                  setError(message(e));
                }
              }}
            >
              <Field label="Current password">
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </Field>
              <Field label="New password (12+ characters)">
                <input
                  name="new_password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  required
                />
              </Field>
              {user.mfa_enabled && (
                <Field label="Authenticator or recovery code">
                  <input name="code" autoComplete="one-time-code" required />
                </Field>
              )}
              <button>Update password</button>
            </form>
          </section>
        </div>
      )}
      <section className="panel account-sessions">
        <div className="account-sessions-heading">
          <div>
            <h2>Active sessions</h2>
            <p className="account-description">
              See where you’re signed in to PulseGrid.
            </p>
          </div>
          <button
            ref={signOutEverywhere}
            className="danger"
            disabled={endingSession}
            onClick={() => setConfirmSignOut(true)}
          >
            Sign out everywhere
          </button>
        </div>
        <ul className="session-list">
          <li className="session-row">
            <div className="account-card-info">
              <span className="account-icon">
                <Laptop size={19} aria-hidden="true" />
              </span>
              <div>
                <div className="session-title">
                  <strong>This browser</strong>
                  <span className="session-current">Current session</span>
                </div>
                {currentSession && (
                  <p className="account-description">
                    Last active {stamp(currentSession.last_activity)}
                  </p>
                )}
              </div>
            </div>
            <button
              disabled={endingSession}
              onClick={() => void endSession("current")}
            >
              <LogOut size={15} aria-hidden="true" />
              Sign out
            </button>
          </li>
          {otherSessions?.map((session) => (
            <li className="session-row" key={session.id}>
              <div className="account-card-info">
                <span className="account-icon">
                  <Laptop size={19} aria-hidden="true" />
                </span>
                <div>
                  <strong>{session.label || "Other browser"}</strong>
                  <p className="account-description">
                    Last active {stamp(session.last_activity)}
                  </p>
                </div>
              </div>
              <button
                disabled={endingSession}
                onClick={() => void endSession(session)}
              >
                End session
              </button>
            </li>
          ))}
        </ul>
        {sessions.isPending && (
          <p className="account-session-note" role="status">
            Loading other sessions…
          </p>
        )}
        {sessions.data && !otherSessions?.length && (
          <p className="account-session-note">No other active sessions.</p>
        )}
      </section>
      {confirmSignOut && (
        <Modal title="Sign out everywhere?" close={closeSignOut}>
          <p>
            This ends every PulseGrid session, including this browser. You’ll
            need to sign in again.
          </p>
          <div className="account-dialog-actions">
            <button onClick={closeSignOut}>Cancel</button>
            <button
              className="danger"
              onClick={() => {
                closeSignOut();
                void endSession("all");
              }}
            >
              Sign out everywhere
            </button>
          </div>
        </Modal>
      )}
      {manage && (
        <Modal
          title={
            manage === "disable"
              ? "Disable two-factor authentication"
              : "Replace recovery codes"
          }
          close={() => setManage(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                const r = await api<{ recovery_codes?: string[] }>(
                  `/auth/mfa/${manage}`,
                  "POST",
                  Object.fromEntries(new FormData(e.currentTarget)),
                );
                if (r.recovery_codes) setCodes(r.recovery_codes);
                setManage(null);
                await client.invalidateQueries();
              } catch (e) {
                setError(message(e));
              }
            }}
          >
            <Field label="Current password">
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </Field>
            <Field label="Authenticator or unused recovery code">
              <input name="code" autoComplete="one-time-code" required />
            </Field>
            <p className="hint">
              Other sessions will be revoked. Replaced recovery codes stop
              working immediately.
            </p>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button className="primary">Confirm</button>
          </form>
        </Modal>
      )}
      {!!codes.length && (
        <Modal title="Save your recovery codes" close={() => setCodes([])}>
          <p>
            Each code works once. Store these securely; they will not be shown
            again.
          </p>
          <div className="recovery-codes">
            {codes.map((c) => (
              <code key={c}>{c}</code>
            ))}
          </div>
          <button
            className="primary"
            onClick={() => {
              void navigator.clipboard.writeText(codes.join("\n"));
              notice("Recovery codes copied.");
            }}
          >
            <Check size={16} />
            Copy codes
          </button>
          <button onClick={() => setCodes([])}>I have saved these</button>
        </Modal>
      )}
    </div>
  );
}
