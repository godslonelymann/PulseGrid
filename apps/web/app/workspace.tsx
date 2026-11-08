"use client";

import {
  Suspense,
  useState,
  useCallback,
  useMemo,
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
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  Laptop,
  LogOut,
  Mail,
  Pause,
  Play,
  Plus,
  Radio,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
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
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { api, ApiError, currentUser } from "./api";
import { AuthButton, AuthField } from "./auth-form";
import { WorkspaceSidebar } from "./workspace-sidebar";
import { Modal, ServiceMenu, Toast } from "./workspace-controls";
import { AuthenticatorCode, PasswordInput } from "./security-fields";

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
  scheduled_for: string;
  lag_ms: number | null;
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
type EmailAlertSettings = {
  email_available: boolean;
  email_enabled: boolean;
  email_channel_id: string | null;
};
type EmailTest = {
  delivered_at: string | null;
  exhausted: boolean;
  last_error: string | null;
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
function useData<T>(key: string, url: string, enabled = true, poll = true) {
  return useQuery<T>({
    queryKey: [key, url],
    queryFn: () => api<T>(url),
    enabled,
    refetchInterval:
      poll &&
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
function Field({
  label,
  children,
  error,
}: {
  label: string;
  children: ReactNode;
  error?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {cloneElement(
        children as ReactElement<{
          id?: string;
          "aria-invalid"?: boolean;
          "aria-describedby"?: string;
        }>,
        {
          id,
          ...(error && {
            "aria-invalid": true,
            "aria-describedby": `${id}-error`,
          }),
        },
      )}
      {error && (
        <p id={`${id}-error`} className="field-error" role="alert">
          {error}
        </p>
      )}
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

export default function Workspace({ signedOutPage }: { signedOutPage?: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<div className="loading">Loading PulseGrid…</div>}>
        <Application signedOutPage={signedOutPage} />
      </Suspense>
    </QueryClientProvider>
  );
}
function Application({ signedOutPage }: { signedOutPage?: ReactNode }) {
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
  if (signedOut && !user.data && signedOutPage) return signedOutPage;

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
      "Sign in with the method you already use for this account. Contact your administrator if your account needs migration.",
  };
  const [error, setError] = useState(
    googleErrors[callback.get("google_error") || ""] || "",
  );
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"login" | "signup" | "reset">(() => {
    const requested = new URLSearchParams(window.location.search).get("auth");
    return requested === "signup" || requested === "reset"
      ? requested
      : "login";
  });
  const [email, setEmail] = useState("");
  const [emailRequest, setEmailRequest] = useState<{
    detail: string;
    resent: boolean;
  }>();
  const headingRef = useRef<HTMLHeadingElement>(null);
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
  const heading = challenge
    ? "Verify your sign-in"
    : emailRequest
      ? "Check your inbox"
      : {
          login: "Welcome to PulseGrid.",
          signup: "Create your account",
          reset: "Forgot your password?",
        }[mode];

  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
  }, [heading]);

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
    if (busy) return;
    setBusy(true);
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      if (challenge) {
        await api("/auth/verify", "POST", { challenge, code: f.get("code") });
        onLogin();
      } else if (mode === "login") {
        const result = await api<{ challenge?: string }>(
          "/auth/login",
          "POST",
          {
            email: f.get("email"),
            password: f.get("password"),
          },
        );
        if (result.challenge) setChallenge(result.challenge);
        else onLogin();
      } else {
        const address = String(f.get("email"));
        setEmail(address);
        await requestEmail(address);
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function requestEmail(address: string, resent = false) {
    const result = await api<{ detail: string }>(
      mode === "signup" ? "/auth/email/signup" : "/auth/email/forgot-password",
      "POST",
      { email: address },
    );
    setEmailRequest({ detail: result.detail, resent });
  }

  async function resend() {
    setBusy(true);
    setError("");
    try {
      await requestEmail(email, true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: typeof mode) {
    const url = new URL(window.location.href);
    url.searchParams.set("auth", next);
    window.history.replaceState(null, "", url);
    setMode(next);
    setError("");
    setEmailRequest(undefined);
  }

  const description = {
    login: "Sign in with Google or your email and password.",
    signup:
      "Enter your email. We’ll send a link so you can confirm it and choose a password.",
    reset: "Enter the email you use for PulseGrid to request a password reset.",
  }[mode];
  const action = {
    login: "Log in",
    signup: "Send confirmation link",
    reset: "Send reset link",
  }[mode];
  return (
    <main className="login">
      <div className="login-art">
        <div className="brand">
          <span className="brand-icon">
            <Activity size={24} />
          </span>
          PulseGrid
        </div>
        <div className="login-copy">
          <h1>Know when your services need attention.</h1>
          <p>Track uptime, response time, and outages in one workspace.</p>
        </div>
      </div>
      <section
        className="login-form auth-card"
        aria-labelledby="sign-in-heading"
      >
        {emailRequest && (
          <span className="auth-mail-icon">
            <Mail size={22} aria-hidden="true" />
          </span>
        )}
        <h2 id="sign-in-heading" ref={headingRef} tabIndex={-1}>
          {heading}
        </h2>
        {emailRequest ? (
          <>
            <p role="status">
              {emailRequest.resent && "Request submitted again. "}
              {emailRequest.detail}
            </p>
            <p>Check your spam folder if you don’t see the email.</p>
            <AuthButton disabled={busy} onClick={() => void resend()}>
              {busy
                ? "Requesting link…"
                : mode === "signup"
                  ? "Resend confirmation email"
                  : "Resend reset email"}
            </AuthButton>
            <div className="auth-actions">
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => switchMode(mode)}
              >
                Use a different email
              </button>
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => switchMode("login")}
              >
                Back to log in
              </button>
            </div>
          </>
        ) : (
          <p>
            {challenge
              ? "Enter your authenticator code or a one-time recovery code."
              : description}
          </p>
        )}
        {!challenge && mode === "login" && (
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
            <div className="auth-divider">or continue with email</div>
          </>
        )}
        {!challenge && !emailRequest && (
          <>
            <form key={mode} onSubmit={submit}>
              <AuthField label="Email">
                <input
                  name="email"
                  type="email"
                  autoComplete={mode === "login" ? "username" : "email"}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </AuthField>
              {mode === "login" && (
                <AuthField label="Password">
                  <PasswordInput
                    name="password"
                    autoComplete="current-password"
                    required
                    maxLength={256}
                  />
                </AuthField>
              )}
              <AuthButton disabled={busy}>
                {busy ? "Please wait…" : action}
              </AuthButton>
            </form>
            <div className="auth-actions">
              {mode === "login" ? (
                <>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => switchMode("signup")}
                  >
                    Create account
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => switchMode("reset")}
                  >
                    Forgot password?
                  </button>
                </>
              ) : (
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => switchMode("login")}
                >
                  Back to log in
                </button>
              )}
            </div>
          </>
        )}
        {challenge && (
          <form onSubmit={submit}>
            <AuthField label="Authentication or recovery code">
              <input name="code" autoComplete="one-time-code" required />
            </AuthField>
            <AuthButton disabled={busy}>
              {busy ? "Please wait…" : "Verify and sign in"}
            </AuthButton>
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
            onClick={() => {
              setChallenge("");
              setError("");
            }}
          >
            Start again
          </button>
        )}
      </section>
    </main>
  );
}
type ServiceOperation = "pause" | "resume" | "archive" | "restore" | "delete";

function Dashboard({ user }: { user: User }) {
  const client = useQueryClient();
  const router = useRouter();
  const params = useSearchParams();
  const requestedView = params.get("view");
  const view = params.get("monitor")
    ? "detail"
    : ["settings", "alerts", "notifications"].includes(requestedView || "")
      ? "settings"
      : "dashboard";
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [modal, setModal] = useState<"monitor" | null>(null);
  const [editing, setEditing] = useState<Monitor | undefined>();
  const [serviceError, setServiceError] = useState<{
    id: string;
    operation: ServiceOperation;
    text: string;
  }>();
  const [notice, setNotice] = useState("");
  const clearNotice = useCallback(() => setNotice(""), []);
  const [pendingService, setPendingService] = useState<{
    id: string;
    operation: ServiceOperation;
  }>();
  const serviceLock = useRef(false);
  const [confirmDelete, setConfirmDelete] = useState<Monitor>();
  const [deleteError, setDeleteError] = useState("");
  const [focusedOrder, setFocusedOrder] = useState<string[] | null>(null);
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
    await api(url, method, body);
    await client.invalidateQueries();
  }
  async function serviceAction(m: Monitor, operation: ServiceOperation) {
    if (serviceLock.current) return;
    serviceLock.current = true;
    setPendingService({ id: m.id, operation });
    setServiceError(undefined);
    setDeleteError("");
    const success = {
      pause: "Service paused",
      resume: "Service resumed",
      archive: "Service archived",
      restore: "Service restored",
      delete: "Service deleted",
    };
    try {
      if (operation === "delete") {
        await act(`/monitors/${m.id}?confirm_history=true`, "DELETE");
        setConfirmDelete(undefined);
        navigate();
      } else {
        await act(`/monitors/${m.id}/${operation}`);
      }
      setNotice(success[operation]);
    } catch (e) {
      if (operation === "delete") setDeleteError(message(e));
      else setServiceError({ id: m.id, operation, text: message(e) });
    } finally {
      serviceLock.current = false;
      setPendingService(undefined);
    }
  }
  const navigate = (v = "dashboard") => {
    setFocusedOrder(null);
    router.push(`/?view=${v}`, { scroll: true });
  };
  const priority: Record<string, number> = {
    DOWN: 0,
    SUSPECT: 1,
    UNKNOWN: 2,
    UP: 3,
    PAUSED: 4,
  };
  const shown = monitors
    .filter((m) =>
      `${m.name} ${m.url}`.toLowerCase().includes(search.trim().toLowerCase()),
    )
    .sort(
      (a, b) =>
        (priority[a.state] ?? 5) - (priority[b.state] ?? 5) ||
        a.name.localeCompare(b.name),
    );
  if (focusedOrder) {
    const positions = new Map(focusedOrder.map((id, index) => [id, index]));
    shown.sort(
      (a, b) =>
        (positions.get(a.id) ?? Infinity) - (positions.get(b.id) ?? Infinity),
    );
  }
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
  const monitoringStopped = !!monitors.length && !!worker.data && !online;
  const summary = showArchived
    ? "Archived services retain their history"
    : data.isPending
      ? "Checking your services…"
      : !data.data && data.error
        ? "Service status is unavailable"
        : !monitors.length
          ? "Your first service starts here"
          : monitoringStopped
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
      <WorkspaceSidebar
        email={user.email}
        inSettings={view === "settings"}
        navigate={navigate}
      />
      <main className="main">
        <header className="topbar">
          <span>
            Workspace <ChevronRight size={14} />
            <strong>
              {view === "detail"
                ? current?.name || "Service"
                : view === "settings"
                  ? "Settings"
                  : "Dashboard"}
            </strong>
          </span>
          <span className="poll-label">Updates automatically</span>
        </header>
        <div className="content">
          {((view === "dashboard" && data.error && data.data) ||
            worker.error) && (
            <div className="alert" role="alert">
              <span>
                {worker.error
                  ? "Monitoring status is unavailable."
                  : "Service status could not refresh. Showing the last loaded results."}
              </span>
              <button onClick={() => void client.invalidateQueries()}>
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
          {view === "dashboard" && (
            <>
              <div className="page-heading">
                <div>
                  <h1>
                    Dashboard<span className="heading-dot">.</span>
                  </h1>
                </div>
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
                className={`status-banner ${!showArchived && (problems || monitoringStopped) ? "needs-attention" : ""}`}
                role="status"
              >
                <span
                  className={`status-orb ${!showArchived && (problems || monitoringStopped) ? "down" : showArchived || unknown || !monitors.length || paused === monitors.length ? "unknown" : ""}`}
                >
                  <Activity size={19} aria-hidden="true" />
                </span>
                <div>
                  <strong>{summary}</strong>
                  <p>
                    {showArchived
                      ? "Restore a service from its detail page to monitor it again."
                      : data.isPending || !data.data
                        ? "Current health appears once your services load."
                        : !monitors.length
                          ? "Add a website or API. We’ll take care of the checks."
                          : monitoringStopped
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
                    <div
                      className="pill-selector"
                      role="group"
                      aria-label="Service list"
                    >
                      <button
                        type="button"
                        aria-pressed={!showArchived}
                        onClick={() => setShowArchived(false)}
                      >
                        Active
                      </button>
                      <button
                        type="button"
                        aria-pressed={showArchived}
                        onClick={() => setShowArchived(true)}
                      >
                        Archived
                      </button>
                    </div>
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
                  <div role="status" className="service-loading">
                    <span className="sr-only">Loading services…</span>
                    {Array.from({ length: 3 }, (_, index) => (
                      <div
                        key={index}
                        className="service-row skeleton-row"
                        aria-hidden="true"
                      >
                        <span />
                        <span />
                        <span />
                        <span />
                      </div>
                    ))}
                  </div>
                ) : !data.data && data.error ? (
                  <div className="service-empty" role="alert">
                    <h2>We couldn’t load your services</h2>
                    <p>{message(data.error)}</p>
                    <button
                      onClick={() => void data.refetch()}
                      disabled={data.isFetching}
                    >
                      Retry
                    </button>
                  </div>
                ) : !shown.length ? (
                  <div className="service-empty">
                    <Radio size={32} aria-hidden="true" />
                    <h2>
                      {monitors.length
                        ? "No search matches"
                        : showArchived
                          ? "No archived services"
                          : "No active services"}
                    </h2>
                    <p>
                      {monitors.length
                        ? "Try a different name or URL."
                        : showArchived
                          ? "Services you archive will appear here."
                          : "Add a website or API to start monitoring."}
                    </p>
                    {monitors.length > 0 && (
                      <button onClick={() => setSearch("")}>
                        Clear search
                      </button>
                    )}
                    {!monitors.length && !showArchived && !atMonitorLimit && (
                      <button className="primary" onClick={openService}>
                        <Plus size={16} />
                        Add your first service
                      </button>
                    )}
                  </div>
                ) : (
                  <ul
                    className="service-list"
                    onFocus={() =>
                      setFocusedOrder(
                        (order) => order ?? shown.map((m) => m.id),
                      )
                    }
                    onBlur={(event) => {
                      if (!event.currentTarget.contains(event.relatedTarget))
                        setFocusedOrder(null);
                    }}
                  >
                    {shown.map((m) => (
                      <li className="service-row" key={m.id}>
                        <button
                          className="monitor-link"
                          onClick={() => {
                            setFocusedOrder(null);
                            router.push(`/?monitor=${m.id}`, { scroll: true });
                          }}
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
                        <Badge
                          state={m.state}
                          label={m.archived_at ? "Archived" : undefined}
                        />
                        <span className="last-check">
                          {m.last_observed ? (
                            <time dateTime={m.last_observed}>
                              <span>Last checked</span>
                              {stamp(m.last_observed)}
                            </time>
                          ) : (
                            "No checks yet"
                          )}
                        </span>
                        <div className="service-actions">
                          {pendingService?.id === m.id && (
                            <span className="row-pending" role="status">
                              Updating…
                            </span>
                          )}
                          {!m.archived_at && (
                            <ServiceMenu
                              name={m.name}
                              actions={[
                                {
                                  label: "Edit",
                                  icon: (
                                    <Settings size={16} aria-hidden="true" />
                                  ),
                                  disabled: !!pendingService,
                                  run: () => {
                                    setEditing(m);
                                    setModal("monitor");
                                  },
                                },
                                {
                                  label: m.paused ? "Resume" : "Pause",
                                  icon: m.paused ? (
                                    <Play size={16} aria-hidden="true" />
                                  ) : (
                                    <Pause size={16} aria-hidden="true" />
                                  ),
                                  disabled: !!pendingService,
                                  run: () =>
                                    void serviceAction(
                                      m,
                                      m.paused ? "resume" : "pause",
                                    ),
                                },
                              ]}
                            />
                          )}
                        </div>
                        {serviceError?.id === m.id && (
                          <p
                            className="row-action-error field-error"
                            role="alert"
                          >
                            Couldn’t {serviceError.operation} {m.name}.{" "}
                            {serviceError.text}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
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
              action={(operation) => void serviceAction(current, operation)}
              pending={
                pendingService?.id === current.id
                  ? pendingService.operation
                  : undefined
              }
              failure={
                serviceError?.id === current.id ? serviceError : undefined
              }
              edit={() => {
                setEditing(current);
                setModal("monitor");
              }}
              remove={() => {
                setDeleteError("");
                setConfirmDelete(current);
              }}
            />
          )}
          {view === "settings" && (
            <Account user={user} act={act} notice={setNotice} />
          )}
        </div>
      </main>
      {modal === "monitor" && (
        <MonitorForm
          monitor={editing}
          act={act}
          close={() => setModal(null)}
          saved={() => {
            setModal(null);
            setShowArchived(false);
            setNotice(editing ? "Service updated" : "Service added");
          }}
        />
      )}
      {confirmDelete && (
        <Modal
          title="Permanently delete service?"
          close={() => setConfirmDelete(undefined)}
          busy={!!pendingService}
        >
          <p>
            Delete <strong>{confirmDelete.name}</strong> and all of its check,
            outage, and configuration history? This action is permanent.
          </p>
          {deleteError && (
            <p className="error" role="alert">
              {deleteError}
            </p>
          )}
          <div className="account-dialog-actions">
            <button
              autoFocus
              disabled={!!pendingService}
              onClick={() => setConfirmDelete(undefined)}
            >
              Cancel
            </button>
            <button
              className="danger"
              disabled={!!pendingService}
              onClick={() => void serviceAction(confirmDelete, "delete")}
            >
              {pendingService ? "Deleting service…" : "Delete permanently"}
            </button>
          </div>
        </Modal>
      )}
      <Toast message={notice} dismiss={clearNotice} />
    </div>
  );
}
function MonitorForm({
  monitor,
  act,
  close,
  saved,
}: {
  monitor?: Monitor;
  act: (u: string, m?: string, b?: unknown) => Promise<void>;
  close: () => void;
  saved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [advanced, setAdvanced] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (lock.current) return;
    const f = new FormData(e.currentTarget);
    const url = String(f.get("url")).trim();
    const interval = Number(f.get("interval"));
    if (!/^https?:\/\//i.test(url)) {
      setErrors({ url: "Enter an HTTP or HTTPS URL." });
      return;
    }
    if (!Number.isInteger(interval) || interval < 30 || interval > 86400) {
      setErrors({
        interval: "Enter a whole number between 30 and 86,400 seconds.",
      });
      setAdvanced(true);
      return;
    }
    lock.current = true;
    setBusy(true);
    setErrors({});
    try {
      await act(
        monitor ? `/monitors/${monitor.id}` : "/monitors",
        monitor ? "PUT" : "POST",
        {
          url,
          name: String(f.get("name") || "").trim(),
          interval,
          ...(monitor && {
            method: monitor.method,
            timeout: monitor.timeout,
            statuses: monitor.statuses,
          }),
        },
      );
      saved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const { url, name, interval } = e.fields;
        const destinationError =
          /destination|url|scheme|hostname|port|http/i.test(e.message);
        if (url || name || interval) setErrors({ url, name, interval });
        else if (destinationError) setErrors({ url: e.message });
        else setErrors({ form: e.message });
        if (interval) setAdvanced(true);
      } else {
        setErrors({ form: message(e) });
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      title={monitor ? "Edit service" : "Add service"}
      close={close}
      busy={busy}
    >
      <form
        onSubmit={submit}
        onChange={(event) => {
          if (event.target instanceof HTMLInputElement) {
            const name = event.target.name;
            setErrors((previous) => ({ ...previous, [name]: "", form: "" }));
          }
        }}
      >
        <Field label="Website or API URL" error={errors.url}>
          <input
            name="url"
            type="url"
            defaultValue={monitor?.url}
            placeholder="https://example.com"
            maxLength={2048}
            required
            onInvalid={(event) => {
              const url = event.currentTarget.validationMessage;
              setErrors((previous) => ({ ...previous, url }));
            }}
            data-dialog-focus
          />
        </Field>
        <Field label="Name (optional)" error={errors.name}>
          <input
            name="name"
            defaultValue={monitor?.name}
            placeholder="Defaults to the hostname"
            maxLength={100}
          />
        </Field>
        <p className="hint">Checks run every 30 seconds by default.</p>
        <details
          className="advanced smooth-accordion"
          open={advanced}
          onToggle={(event) => setAdvanced(event.currentTarget.open)}
        >
          <summary>
            Advanced settings
            <ChevronDown
              className="accordion-chevron"
              size={15}
              aria-hidden="true"
            />
          </summary>
          <div className="advanced-body">
            <Field label="Check frequency (seconds)" error={errors.interval}>
              <input
                type="number"
                name="interval"
                min={30}
                max={86400}
                defaultValue={monitor?.interval || 30}
                onInvalid={(event) => {
                  const interval = event.currentTarget.validationMessage;
                  setErrors((previous) => ({ ...previous, interval }));
                  setAdvanced(true);
                }}
                required
              />
            </Field>
          </div>
        </details>
        {errors.form && (
          <p className="error" role="alert">
            {errors.form}
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
const RECENT_CHECKS = 12;
const METRIC_WINDOWS = [
  [1, "1h"],
  [24, "24h"],
  [168, "7d"],
  [720, "30d"],
] as const;

function outageDuration(start: string, end: number) {
  const seconds = Math.max(0, Math.floor((end - Date.parse(start)) / 1000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days) return `${days}d ${hours % 24}h`;
  if (hours) return `${hours}h ${minutes % 60}m`;
  if (minutes) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

function RecentCheckStrip({ results }: { results: Result[] }) {
  const [selectedId, setSelectedId] = useState("");
  const ordered = results.slice(0, RECENT_CHECKS).reverse();
  const selected =
    ordered.find((check) => check.id === selectedId) ?? ordered.at(-1);
  return (
    <>
      <ol
        className="check-strip"
        aria-label="Observed checks, oldest to newest"
      >
        {ordered.map((check) => (
          <li key={check.id}>
            <button
              type="button"
              className={check.success ? "check-passed" : "check-failed"}
              aria-pressed={selected?.id === check.id}
              aria-label={`${check.success ? "Passed" : "Failed"}, ${stamp(check.observed_at)}`}
              onClick={() => setSelectedId(check.id)}
            >
              {check.success ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                <X size={15} aria-hidden="true" />
              )}
            </button>
          </li>
        ))}
      </ol>
      {selected && (
        <p className="check-inspection" aria-live="polite">
          <strong>{selected.success ? "Passed" : "Failed"}</strong> ·{" "}
          {stamp(selected.observed_at)}
          {selected.status_code != null && ` · HTTP ${selected.status_code}`}
          {selected.latency_ms != null && ` · ${ms(selected.latency_ms)}`}
        </p>
      )}
    </>
  );
}

function MonitorDetail({
  monitor: m,
  back,
  action,
  pending,
  failure,
  edit,
  remove,
}: {
  monitor: Monitor;
  back: () => void;
  action: (operation: "pause" | "resume" | "archive" | "restore") => void;
  pending?: ServiceOperation;
  failure?: { operation: ServiceOperation; text: string };
  edit: () => void;
  remove: () => void;
}) {
  const busy = !!pending;
  const statusAction = pending === "pause" || pending === "resume";
  const statusFailure =
    failure?.operation === "pause" || failure?.operation === "resume";
  const [hours, setHours] = useState(24);
  const [checkPage, setCheckPage] = useState(0);
  const [incidentOffset, setIncidentOffset] = useState(0);
  const latest = useData<Result[]>(
    "results",
    `/monitors/${m.id}/results?hours=${hours}&limit=${RECENT_CHECKS}`,
  );
  const older = useData<Result[]>(
    "results",
    `/monitors/${m.id}/results?hours=${hours}&limit=${CHECKS_PER_PAGE + 1}&offset=${checkPage * CHECKS_PER_PAGE}`,
    checkPage > 0,
    false,
  );
  const checks = checkPage === 0 ? latest : older;
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
  const trend = useMemo(
    () =>
      stats?.latency_trend.map((bucket) => ({
        time: Date.parse(bucket.starts_at),
        latency: bucket.median_ms,
      })),
    [stats],
  );
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
          <Badge
            state={m.state}
            label={m.archived_at ? "Archived" : undefined}
          />
          {!m.archived_at && (
            <>
              <button
                aria-disabled={busy}
                onClick={() => !busy && action(m.paused ? "resume" : "pause")}
              >
                {m.paused ? <Play size={15} /> : <Pause size={15} />}
                {m.paused ? "Resume" : "Pause"}
              </button>
              <button aria-disabled={busy} onClick={() => !busy && edit()}>
                Edit service
              </button>
            </>
          )}
        </div>
      </div>
      <p className="hint">
        {m.archived_at
          ? "Archived · history retained"
          : m.paused
            ? "Monitoring paused"
            : "Monitoring enabled"}{" "}
        · Last checked {stamp(m.last_observed)} · Every {m.interval}s
      </p>
      {statusAction && (
        <p className="hint" role="status">
          {pending === "pause" ? "Pausing monitoring…" : "Resuming monitoring…"}
        </p>
      )}
      {statusFailure && (
        <p className="error" role="alert">
          Couldn’t {failure.operation} {m.name}. {failure.text}
        </p>
      )}
      {(versions.data?.length || 0) > 1 && (
        <p className="hint">
          History includes checks made with earlier settings. See Configuration
          history in More details.
        </p>
      )}
      <section className="panel history-overview" aria-label="Service history">
        <div className="panel-toolbar">
          <h2>Service history</h2>
          <div
            className="pill-selector range-selector"
            role="group"
            aria-label="Metric window"
          >
            {METRIC_WINDOWS.map(([value, label]) => (
              <button
                key={value}
                aria-pressed={hours === value}
                onClick={() => {
                  if (value === hours) return;
                  setHours(value);
                  setCheckPage(0);
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="stat-grid simple-metrics">
          <section className="stat">
            <div>Uptime</div>
            <strong className="metric-value">{pct(stats?.uptime)}</strong>
            <small>Successful checks in the selected period</small>
          </section>
          <section className="stat">
            <div>Typical response time</div>
            <strong className="metric-value">{ms(stats?.median_ms)}</strong>
            <small>Median time to response headers</small>
          </section>
        </div>
      </section>
      {metrics.error && (
        <div className="alert" role="alert">
          <span>
            {stats
              ? "Response metrics could not refresh."
              : message(metrics.error)}
          </span>
          <button onClick={() => void metrics.refetch()}>Retry</button>
        </div>
      )}
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
        ) : metrics.error && !stats ? (
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
              <AreaChart data={trend} accessibilityLayer>
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
                  filterNull={false}
                  cursor={{ stroke: "#65764e", strokeDasharray: "4 3" }}
                  contentStyle={{ borderRadius: 8, borderColor: "var(--line)" }}
                  labelFormatter={(value) =>
                    new Date(Number(value)).toLocaleString()
                  }
                  formatter={(value) =>
                    value == null
                      ? "No successful timing in this interval"
                      : ms(Number(value))
                  }
                />
                <Area
                  isAnimationActive={false}
                  dataKey="latency"
                  name="Median response time"
                  stroke="#667d46"
                  fill="url(#latency)"
                  connectNulls={false}
                  dot={{ r: 1.5, fill: "#667d46", strokeWidth: 0 }}
                  activeDot={{
                    r: 5,
                    fill: "#4e662f",
                    stroke: "#fff",
                    strokeWidth: 2,
                  }}
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
      <section className="panel recent-checks">
        <div className="panel-toolbar">
          <h2>Recent checks</h2>
          <span className="muted">Oldest to newest</span>
        </div>
        {latest.data?.length ? (
          <RecentCheckStrip results={latest.data} />
        ) : latest.isPending ? (
          <Empty>Loading recent checks…</Empty>
        ) : !latest.error ? (
          <Empty>No observed checks in this window.</Empty>
        ) : null}
        {latest.error && (
          <div className="check-inspection" role="alert">
            {latest.data
              ? "Recent checks could not refresh."
              : "Recent checks are unavailable."}{" "}
            <button onClick={() => void latest.refetch()}>Retry</button>
          </div>
        )}
        {stats && (
          <p className="coverage-summary">
            <strong>{stats.missing} missing</strong> out of {stats.expected}{" "}
            expected checks in the selected window
            {stats.coverage != null && ` · ${pct(stats.coverage)} coverage`}.
            The strip shows completed checks only.
          </p>
        )}
      </section>
      <section className="panel outage-panel">
        <div className="panel-toolbar">
          <h2>Recent outages</h2>
          <span className="muted">Confirmed after two failed checks</span>
        </div>
        {incidents.error && (
          <p className="error" role="alert">
            Outage history could not load.{" "}
            <button onClick={() => void incidents.refetch()}>Retry</button>
          </p>
        )}
        {!incidents.data?.length ? (
          <Empty>
            {incidents.isPending
              ? "Loading outages…"
              : incidents.error
                ? "Outage history is unavailable."
                : "No outages recorded on this page."}
          </Empty>
        ) : (
          <ol className="outage-timeline">
            {incidents.data.map((i) => (
              <li key={i.id} data-open={!i.closed_at}>
                <div className="outage-state">
                  <Badge
                    state={i.closed_at ? "UP" : "DOWN"}
                    label={i.closed_at ? "Recovered" : "Down"}
                  />
                  <strong>
                    {outageDuration(
                      i.opened_at,
                      i.closed_at
                        ? Date.parse(i.closed_at)
                        : incidents.dataUpdatedAt,
                    )}
                  </strong>
                </div>
                <dl>
                  <div>
                    <dt>First detected</dt>
                    <dd>
                      <time dateTime={i.opened_at}>{stamp(i.opened_at)}</time>
                    </dd>
                  </div>
                  <div>
                    <dt>Recovered</dt>
                    <dd>
                      {i.closed_at ? (
                        <time dateTime={i.closed_at}>{stamp(i.closed_at)}</time>
                      ) : (
                        "No recovery observed"
                      )}
                    </dd>
                  </div>
                </dl>
              </li>
            ))}
          </ol>
        )}
        <nav className="pagination" aria-label="Outage pagination">
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
        </nav>
      </section>
      <details className="advanced service-more smooth-accordion">
        <summary>
          <span className="service-more-icon" aria-hidden="true">
            <SlidersHorizontal size={16} />
          </span>
          <span>More details</span>
          <ChevronDown
            className="accordion-chevron"
            size={17}
            aria-hidden="true"
          />
        </summary>
        <div className="advanced-body">
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
            {versions.data?.length ? (
              <ol className="configuration-ledger">
                {versions.data.map((v) => (
                  <li key={v.id}>
                    <div>
                      <strong>
                        {v.active ? "Active" : "Paused / archived"}
                      </strong>
                      <Badge
                        state={v.ends_at ? "PAUSED" : "UP"}
                        label={v.ends_at ? "Historical" : "Current"}
                      />
                    </div>
                    <p>
                      {v.settings_known ? v.url : "Earlier URL not recorded"}
                    </p>
                    <dl>
                      <div>
                        <dt>Effective</dt>
                        <dd>
                          <time dateTime={v.starts_at}>
                            {stamp(v.starts_at)}
                          </time>
                        </dd>
                      </div>
                      <div>
                        <dt>Until</dt>
                        <dd>
                          {v.ends_at ? (
                            <time dateTime={v.ends_at}>{stamp(v.ends_at)}</time>
                          ) : (
                            "Current"
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>Check</dt>
                        <dd>
                          {v.method || "Unknown method"} · every {v.interval}s
                        </dd>
                      </div>
                    </dl>
                  </li>
                ))}
              </ol>
            ) : (
              !versions.error && (
                <Empty>
                  {versions.isPending
                    ? "Loading configuration history…"
                    : "No configuration changes recorded."}
                </Empty>
              )
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
              <h2>Check diagnostics</h2>
              <span className="muted">Newest first</span>
            </div>
            <ul className="check-diagnostics">
              {rows.map((r) => (
                <li key={r.id}>
                  <details>
                    <summary>
                      <time dateTime={r.observed_at}>
                        {stamp(r.observed_at)}
                      </time>
                      <Badge
                        state={r.success ? "UP" : "DOWN"}
                        label={r.success ? "Passed" : "Failed"}
                      />
                      <span>{ms(r.latency_ms)}</span>
                      <ChevronDown size={16} aria-hidden="true" />
                    </summary>
                    <dl>
                      <div>
                        <dt>HTTP status</dt>
                        <dd>{r.status_code ?? "No response"}</dd>
                      </div>
                      <div>
                        <dt>Scheduled for</dt>
                        <dd>{stamp(r.scheduled_for)}</dd>
                      </div>
                      <div>
                        <dt>Check delay</dt>
                        <dd>{ms(r.lag_ms)}</dd>
                      </div>
                      <div>
                        <dt>Result</dt>
                        <dd>
                          {r.error ||
                            (r.success ? "Accepted response" : "Check failed")}
                        </dd>
                      </div>
                    </dl>
                  </details>
                </li>
              ))}
            </ul>
            {checks.error && (
              <p className="error" role="alert">
                Check diagnostics could not load.{" "}
                <button onClick={() => void checks.refetch()}>Retry</button>
              </p>
            )}
            {checks.isPending ? (
              <Empty>Loading checks…</Empty>
            ) : !checks.error && !rows.length ? (
              <Empty>No completed checks on this page.</Empty>
            ) : null}
            <nav
              className="pagination"
              aria-label="Check diagnostics pagination"
            >
              <button
                aria-disabled={checkPage === 0}
                onClick={() => setCheckPage((page) => Math.max(0, page - 1))}
              >
                Previous
              </button>
              <span className="muted" aria-live="polite">
                Page {checkPage + 1}
              </span>
              <button
                aria-disabled={
                  !hasMoreChecks || checkPage * CHECKS_PER_PAGE >= 100000
                }
                onClick={() => {
                  if (hasMoreChecks && checkPage * CHECKS_PER_PAGE < 100000)
                    setCheckPage((page) => page + 1);
                }}
              >
                Next
              </button>
            </nav>
          </section>
        </div>
      </details>
      <section
        className="panel lifecycle-panel"
        aria-label="Service management"
      >
        <div>
          <div>
            <h2>{m.archived_at ? "Restore monitoring" : "Archive service"}</h2>
            <p className="hint">
              {m.archived_at
                ? "Return this service to the active list and keep its history."
                : "Stop monitoring and keep all service history."}
            </p>
          </div>
          <button
            aria-disabled={busy}
            onClick={() =>
              !busy && action(m.archived_at ? "restore" : "archive")
            }
          >
            {m.archived_at ? "Restore service" : "Archive service"}
          </button>
        </div>
        <div>
          <div>
            <h2>Permanent deletion</h2>
            <p className="hint">
              Remove this service and its check, outage, and configuration
              history.
            </p>
          </div>
          <button
            className="danger"
            aria-disabled={busy}
            onClick={() => !busy && remove()}
          >
            Delete
          </button>
        </div>
        {(pending === "archive" || pending === "restore") && (
          <p className="lifecycle-feedback hint" role="status">
            {pending === "archive"
              ? "Archiving service…"
              : "Restoring service…"}
          </p>
        )}
        {failure && !statusFailure && (
          <p className="lifecycle-feedback field-error" role="alert">
            Couldn’t {failure.operation} {m.name}. {failure.text}
          </p>
        )}
      </section>
    </>
  );
}
function EmailAlertSettingsCard({
  email,
  act,
  notice,
}: {
  email: string;
  notice: (text: string) => void;
  act: (url: string, method?: string, body?: unknown) => Promise<void>;
}) {
  const settings = useData<EmailAlertSettings>(
    "notification-settings",
    "/notification-settings",
  );
  const tests = useData<EmailTest[]>(
    "deliveries",
    "/deliveries?channel_kind=email&event_kind=test&limit=1",
  );
  const [busy, setBusy] = useState<"settings" | "test" | null>(null);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const latest = tests.data?.[0];
  const channelId = settings.data?.email_channel_id;
  let testStatus = "No tests sent yet.";
  if (tests.error) testStatus = "Status unavailable.";
  else if (tests.isPending) testStatus = "Loading…";
  else if (latest?.delivered_at)
    testStatus = `Delivered ${stamp(latest.delivered_at)}`;
  else if (latest?.exhausted) testStatus = "Failed.";
  else if (latest && settings.data?.email_enabled === false)
    testStatus = "Paused while email alerts are off.";
  else if (latest?.last_error)
    testStatus = "Failed. Another attempt is scheduled.";
  else if (latest) testStatus = "Queued.";

  async function update(kind: "settings" | "test", enabled?: boolean) {
    if (lock.current || (kind === "test" && !channelId)) return;
    lock.current = true;
    setBusy(kind);
    setError("");
    try {
      if (kind === "test") {
        await act(`/notification-channels/${channelId}/test`, "POST");
        notice("Test queued");
      } else {
        await act("/notification-settings", "PUT", { enabled });
      }
    } catch (e) {
      setError(message(e));
    } finally {
      lock.current = false;
      setBusy(null);
    }
  }

  return (
    <section className="panel email-alerts" aria-label="Email alerts">
      <div className="account-card-info">
        <span className="account-icon">
          <Bell size={20} aria-hidden="true" />
        </span>
        <div>
          <h2>Email alerts</h2>
          <p className="account-description">
            PulseGrid uses {email} for service outage and recovery alerts.
          </p>
        </div>
      </div>
      {settings.isPending && <p className="hint">Loading email settings…</p>}
      {settings.error && (
        <p className="error" role="alert">
          Email settings are unavailable.
        </p>
      )}
      {settings.data && (
        <>
          <label className="checkbox">
            <input
              type="checkbox"
              role="switch"
              checked={settings.data.email_enabled}
              aria-disabled={!!busy}
              onClick={(event) => {
                if (busy) event.preventDefault();
              }}
              onChange={(event) =>
                void update("settings", event.target.checked)
              }
            />
            Email me about outages and recoveries
          </label>
          <button
            disabled={!settings.data.email_available || !channelId}
            aria-disabled={
              !!busy || !settings.data.email_available || !channelId
            }
            onClick={() => void update("test")}
          >
            {busy === "test" ? "Queuing test…" : "Send test email"}
          </button>
          {!settings.data.email_available && (
            <p className="hint">
              Email delivery hasn’t been set up on this server.
            </p>
          )}
        </>
      )}
      <p className="account-description" role="status">
        <strong>Latest test</strong>
        <span
          className="delivery-state"
          data-state={testStatus.split(/[ .]/)[0].toLowerCase()}
        >
          {testStatus}
        </span>
      </p>
      {latest?.last_error && !tests.error && (
        <p className="error">{latest.last_error}</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
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
  const [linkStatus] = useState(() =>
    new URLSearchParams(window.location.hash.slice(1)).get("google_link"),
  );
  useEffect(() => {
    if (linkStatus)
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
  }, [linkStatus]);
  const linkFeedback: Record<string, string> = {
    connected: "Google is now connected to this account.",
    check_email:
      "Check your email to finish adding a password. Open the link in this browser.",
    wrong_account:
      "Choose the Google account that matches your PulseGrid account.",
    expired: "The request expired. Start again from Settings.",
    cancelled: "Google verification was cancelled.",
    failed: "Google verification failed. Try again.",
    mail_unavailable: "Account email is temporarily unavailable.",
  };
  const sessions = useData<BrowserSession[]>("sessions", "/sessions");
  const currentSession = sessions.data?.find((session) => session.current);
  const otherSessions = sessions.data?.filter((session) => !session.current);
  const [error, setError] = useState("");
  const [endingSession, setEndingSession] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const sessionLock = useRef(false);
  const securityLock = useRef(false);
  const [securityBusy, setSecurityBusy] = useState(false);
  const [securityError, setSecurityError] = useState<{
    form: string;
    text: string;
  }>();
  const [copyError, setCopyError] = useState("");
  const [copied, setCopied] = useState("");
  const clearCopied = useCallback(() => setCopied(""), [setCopied]);
  const closeCodes = () => {
    setCodes([]);
    setCopyError("");
    clearCopied();
  };
  const [enrollment, setEnrollment] = useState<{
    secret: string;
    uri: string;
    qr: string;
  } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [manage, setManage] = useState<"disable" | "recovery-codes" | null>(
    null,
  );
  async function submitSecurity(
    event: FormEvent<HTMLFormElement>,
    form: string,
    submit: (fields: FormData) => Promise<void>,
  ) {
    event.preventDefault();
    if (securityLock.current) return;
    const fields = new FormData(event.currentTarget);
    securityLock.current = true;
    setSecurityBusy(true);
    setSecurityError(undefined);
    try {
      await submit(fields);
    } catch (e) {
      setSecurityError({ form, text: message(e) });
    } finally {
      securityLock.current = false;
      setSecurityBusy(false);
    }
  }
  const errorFor = (form: string) =>
    securityError?.form === form ? securityError.text : undefined;
  async function endSession(target: "current" | "all" | BrowserSession) {
    if (sessionLock.current) return;
    sessionLock.current = true;
    setError("");
    setEndingSession(true);
    try {
      if (typeof target === "object") {
        await act(`/sessions/${target.id}`, "DELETE");
        notice("Session ended.");
      } else {
        await act(target === "all" ? "/auth/logout-all" : "/auth/logout");
        client.clear();
        setConfirmSignOut(false);
      }
    } catch (e) {
      setError(message(e));
    } finally {
      sessionLock.current = false;
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
      {((error && !confirmSignOut) || sessions.error) && (
        <p className="error" role="alert">
          {(!confirmSignOut && error) || message(sessions.error)}
        </p>
      )}
      {linkStatus && linkFeedback[linkStatus] && (
        <p
          role={
            ["connected", "check_email"].includes(linkStatus)
              ? "status"
              : "alert"
          }
        >
          {linkFeedback[linkStatus]}
        </p>
      )}
      {user.google_connected && !user.password_enabled && (
        <section className="panel settings-panel">
          <h2>Add a password</h2>
          <p>
            Verify your Google sign-in, then confirm your email to add a
            password.
          </p>
          <form
            onSubmit={(event) =>
              void submitSecurity(event, "add-password", async () => {
                const result = await api<{ authorization_url: string }>(
                  "/auth/google/add-password",
                  "POST",
                );
                window.location.assign(result.authorization_url);
              })
            }
          >
            {errorFor("add-password") && (
              <p className="error" role="alert">
                {errorFor("add-password")}
              </p>
            )}
            <button disabled={securityBusy}>Verify with Google</button>
          </form>
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
                onSubmit={(event) =>
                  void submitSecurity(event, "enroll", async (fields) => {
                    const r = await api<{ secret: string; uri: string }>(
                      "/auth/mfa/enroll",
                      "POST",
                      { password: fields.get("password") },
                    );
                    setEnrollment({ ...r, qr: await QRCode.toDataURL(r.uri) });
                  })
                }
              >
                <Field label="Current password" error={errorFor("enroll")}>
                  <PasswordInput
                    name="password"
                    autoComplete="current-password"
                    required
                  />
                </Field>
                <button className="primary" disabled={securityBusy}>
                  Set up authenticator
                </button>
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
                  onSubmit={(event) =>
                    void submitSecurity(event, "confirm", async (fields) => {
                      const r = await api<{ recovery_codes: string[] }>(
                        "/auth/mfa/confirm",
                        "POST",
                        { code: fields.get("code") },
                      );
                      setCodes(r.recovery_codes);
                      setEnrollment(null);
                      await client.invalidateQueries();
                    })
                  }
                >
                  <Field
                    label="Six-digit authenticator code"
                    error={errorFor("confirm")}
                  >
                    <AuthenticatorCode />
                  </Field>
                  <button className="primary" disabled={securityBusy}>
                    Confirm enrollment
                  </button>
                </form>
              </>
            )}
            {user.mfa_enabled && (
              <div className="button-group">
                <button
                  onClick={() => {
                    setSecurityError(undefined);
                    setManage("recovery-codes");
                  }}
                >
                  Replace recovery codes
                </button>
                <button
                  className="danger"
                  onClick={() => {
                    setSecurityError(undefined);
                    setManage("disable");
                  }}
                >
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
              onSubmit={(event) =>
                void submitSecurity(event, "password", async (fields) => {
                  await act(
                    "/auth/password",
                    "POST",
                    Object.fromEntries(fields),
                  );
                  client.clear();
                })
              }
            >
              <Field label="Current password">
                <PasswordInput
                  name="password"
                  autoComplete="current-password"
                  required
                />
              </Field>
              <Field label="New password (12+ characters)">
                <PasswordInput
                  name="new_password"
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
              {errorFor("password") && (
                <p className="error" role="alert">
                  {errorFor("password")}
                </p>
              )}
              <button disabled={securityBusy}>Update password</button>
            </form>
          </section>
        </div>
      )}
      <EmailAlertSettingsCard email={user.email} act={act} notice={notice} />
      <section className="panel account-sessions">
        <div className="account-sessions-heading">
          <div>
            <h2>Active sessions</h2>
            <p className="account-description">
              See where you’re signed in to PulseGrid.
            </p>
          </div>
          <button
            className="danger"
            disabled={endingSession}
            onClick={() => {
              setError("");
              setConfirmSignOut(true);
            }}
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
              Sign out of this browser
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
                End this session
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
        <Modal
          title="Sign out everywhere?"
          close={() => setConfirmSignOut(false)}
          busy={endingSession}
        >
          <p>
            This ends every PulseGrid session, including this browser. You’ll
            need to sign in again.
          </p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="account-dialog-actions">
            <button
              data-dialog-focus
              disabled={endingSession}
              onClick={() => setConfirmSignOut(false)}
            >
              Cancel
            </button>
            <button
              className="danger"
              disabled={endingSession}
              onClick={() => void endSession("all")}
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
          busy={securityBusy}
        >
          <form
            onSubmit={(event) =>
              void submitSecurity(event, manage, async (fields) => {
                const r = await api<{ recovery_codes?: string[] }>(
                  `/auth/mfa/${manage}`,
                  "POST",
                  Object.fromEntries(fields),
                );
                if (r.recovery_codes) setCodes(r.recovery_codes);
                setManage(null);
                await client.invalidateQueries();
              })
            }
          >
            <Field label="Current password">
              <PasswordInput
                name="password"
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
            {errorFor(manage) && (
              <p className="error" role="alert">
                {errorFor(manage)}
              </p>
            )}
            <button className="primary" disabled={securityBusy}>
              Confirm
            </button>
          </form>
        </Modal>
      )}
      {!!codes.length && (
        <Modal title="Save your recovery codes" close={closeCodes}>
          <p>
            Each code works once. Store these securely; they will not be shown
            again.
          </p>
          <div className="recovery-codes">
            {codes.map((c) => (
              <code key={c}>{c}</code>
            ))}
          </div>
          <div className="account-dialog-actions">
            <button
              className="primary"
              onClick={async () => {
                setCopyError("");
                try {
                  await navigator.clipboard.writeText(codes.join("\n"));
                  setCopied("Recovery codes copied");
                } catch {
                  setCopyError(
                    "Couldn’t access the clipboard. Select the codes to copy them manually.",
                  );
                }
              }}
            >
              <Check size={16} />
              Copy codes
            </button>
            <button onClick={closeCodes}>I have saved these</button>
          </div>
          {copyError && (
            <p className="error" role="alert">
              {copyError}
            </p>
          )}
          <Toast message={copied} dismiss={clearCopied} />
        </Modal>
      )}
    </div>
  );
}
