"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { api, ApiError, currentUser } from "../../api";
import { AuthButton, AuthField } from "../../auth-form";
import { PasswordInput } from "../../security-fields";

const actions = {
  confirm: {
    heading: "Choose a password",
    button: "Create account",
    endpoint: "/auth/email/confirm",
    success: "Your email is confirmed. You can now log in.",
  },
  reset: {
    heading: "Reset your password",
    button: "Reset password",
    endpoint: "/auth/email/reset-password",
    success:
      "Your password has been reset and your existing sessions have ended.",
  },
  "add-password": {
    heading: "Add a password",
    button: "Add password",
    endpoint: "/auth/email/add-password",
    success: "You can now sign in with your email and password or Google.",
  },
};

type Flow = keyof typeof actions;

type EmailAction = { flow: Flow; token: string };

export default function EmailLink() {
  const [request, setRequest] = useState<{
    link: EmailAction | null;
    attempt: number;
  }>();

  useEffect(() => {
    let mounted = true;
    function readLink() {
      if (!mounted) return;
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const flow = fragment.get("flow");
      const token = fragment.get("token");
      const link: EmailAction | null =
        token &&
        (flow === "confirm" || flow === "reset" || flow === "add-password")
          ? { flow, token }
          : null;
      if (link) window.history.replaceState(null, "", window.location.pathname);
      setRequest((previous) => ({
        link,
        attempt: (previous?.attempt ?? 0) + 1,
      }));
    }

    queueMicrotask(readLink);
    window.addEventListener("hashchange", readLink);
    return () => {
      mounted = false;
      window.removeEventListener("hashchange", readLink);
    };
  }, []);

  return (
    <main className="email-link">
      <section
        className="login-form auth-card"
        aria-labelledby="email-link-heading"
      >
        {request?.link ? (
          <EmailLinkForm key={request.attempt} link={request.link} />
        ) : (
          <>
            <h2 id="email-link-heading">PulseGrid account</h2>
            {request ? (
              <p role="alert">
                This link is invalid. Request a new one from the sign-in page.
              </p>
            ) : (
              <p>Opening link…</p>
            )}
            <div className="auth-actions">
              <Link href="/">Back to log in</Link>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function EmailLinkForm({ link }: { link: EmailAction }) {
  const [factor, setFactor] = useState<"required" | "none" | "unavailable">();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (link.flow === "add-password") {
      void currentUser<{ mfa_enabled: boolean }>()
        .then((user) => setFactor(user.mfa_enabled ? "required" : "none"))
        .catch(() => setFactor("unavailable"));
    }
  }, [link.flow]);

  const checking = link.flow === "add-password" && factor === undefined;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || checking) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api(actions[link.flow].endpoint, "POST", {
        token: link.token,
        password: data.get("password"),
        ...(link.flow === "add-password"
          ? { code: data.get("code") || "" }
          : {}),
      });
      setDone(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) setExpired(true);
      setError(e instanceof Error ? e.message : "Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const action = actions[link.flow];
  return (
    <>
      <h2 id="email-link-heading">
        {done && link.flow === "confirm" ? "Account created" : action.heading}
      </h2>
      {factor === "unavailable" && (
        <p className="error" role="alert">
          Open this link in the browser where you started adding a password.
        </p>
      )}
      {factor !== "unavailable" &&
        !expired &&
        (done ? (
          <p role="status">{action.success}</p>
        ) : (
          <form onSubmit={submit}>
            <AuthField label="New password (12+ characters)">
              <PasswordInput
                name="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={256}
                required
              />
            </AuthField>
            {factor === "required" && (
              <AuthField label="Authentication or recovery code">
                <input
                  name="code"
                  autoComplete="one-time-code"
                  maxLength={64}
                  required
                />
              </AuthField>
            )}
            <AuthButton disabled={busy || checking}>
              {busy ? "Please wait…" : action.button}
            </AuthButton>
          </form>
        ))}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="auth-actions">
        {link.flow === "add-password" ? (
          <Link href="/?view=settings">Back to settings</Link>
        ) : (
          <>
            {expired && (
              <Link
                href={link.flow === "reset" ? "/?auth=reset" : "/?auth=signup"}
              >
                Request a new link
              </Link>
            )}
            <Link href="/" className={done ? "auth-login-link" : undefined}>
              {done ? "Log in" : "Back to log in"}
            </Link>
          </>
        )}
      </div>
    </>
  );
}
