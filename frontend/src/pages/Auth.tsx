import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api, errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Logo } from "../components/Layout";
import { Button, Field, Notice } from "../components/ui";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Mirrors the server's password policy so users see problems before submitting. */
export function passwordProblems(password: string): string[] {
  const problems: string[] = [];
  if (password.length < 10) problems.push("at least 10 characters");
  if (password.toLowerCase() === password || password.toUpperCase() === password) problems.push("upper- and lower-case letters");
  if (!/\d/.test(password)) problems.push("a digit");
  return problems;
}

function AuthShell({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Link to="/" className="mb-8" aria-label="FinSense AI home">
        <Logo />
      </Link>
      <div className="panel w-full max-w-md p-6">
        <h1 className="text-2xl">{title}</h1>
        <div className="mt-5">{children}</div>
      </div>
      {footer && <div className="mt-4 text-sm text-muted">{footer}</div>}
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const next: typeof errors = {};
    if (!EMAIL_RE.test(email.trim())) next.email = "Enter a valid e-mail address.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      await login(email.trim(), password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : "/dashboard", { replace: true });
    } catch (error) {
      setErrors({ form: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Sign in"
      footer={
        <>
          New here? <Link className="text-ledger hover:underline" to="/register">Create an account</Link>
          {" · "}
          <Link className="text-ledger hover:underline" to="/forgot-password">Forgot password</Link>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="E-mail" error={errors.email}>
          {(id) => (
            <input id={id} className="field-input" type="email" autoComplete="email" value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <Field label="Password" error={errors.password}>
          {(id) => (
            <input id={id} className="field-input" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} />
          )}
        </Field>
        {errors.form && <Notice tone="loss">{errors.form}</Notice>}
        <Button type="submit" className="w-full" busy={busy}>
          Sign in
        </Button>
      </form>
    </AuthShell>
  );
}

export function RegisterPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ displayName: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const next: Record<string, string> = {};
    if (!form.displayName.trim()) next.displayName = "Enter your name.";
    if (!EMAIL_RE.test(form.email.trim())) next.email = "Enter a valid e-mail address.";
    const problems = passwordProblems(form.password);
    if (problems.length) next.password = `Password needs ${problems.join(", ")}.`;
    if (form.password !== form.confirm) next.confirm = "Passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      await api.post("/auth/register", {
        email: form.email.trim(),
        password: form.password,
        display_name: form.displayName.trim(),
      });
      await login(form.email.trim(), form.password);
      navigate("/dashboard", { replace: true });
    } catch (error) {
      setErrors({ form: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <AuthShell
      title="Create your account"
      footer={<>Already registered? <Link className="text-ledger hover:underline" to="/login">Sign in</Link></>}
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" error={errors.displayName}>
          {(id) => <input id={id} className="field-input" autoComplete="name" value={form.displayName} onChange={set("displayName")} />}
        </Field>
        <Field label="E-mail" error={errors.email}>
          {(id) => <input id={id} className="field-input" type="email" autoComplete="email" value={form.email} onChange={set("email")} />}
        </Field>
        <Field label="Password" hint="At least 10 characters with upper- and lower-case letters and a digit." error={errors.password}>
          {(id) => <input id={id} className="field-input" type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Field label="Repeat password" error={errors.confirm}>
          {(id) => <input id={id} className="field-input" type="password" autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />}
        </Field>
        {errors.form && <Notice tone="loss">{errors.form}</Notice>}
        <Button type="submit" className="w-full" busy={busy}>
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<{ tone: "info" | "loss" | "caution"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!EMAIL_RE.test(email.trim())) {
      setMessage({ tone: "loss", text: "Enter a valid e-mail address." });
      return;
    }
    setBusy(true);
    try {
      const result = await api.post<{ message: string }>("/auth/password-reset/request", { email: email.trim() });
      setMessage({ tone: "info", text: result.message });
    } catch (error) {
      const unavailable = error instanceof ApiError && error.status === 503;
      setMessage({ tone: unavailable ? "caution" : "loss", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Reset your password" footer={<Link className="text-ledger hover:underline" to="/login">Back to sign in</Link>}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="E-mail">
          {(id) => <input id={id} className="field-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}
        </Field>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <Button type="submit" className="w-full" busy={busy}>
          Send reset link
        </Button>
      </form>
    </AuthShell>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problems = passwordProblems(password);
    if (problems.length) {
      setError(`Password needs ${problems.join(", ")}.`);
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/password-reset/confirm", { token, new_password: password });
      navigate("/login", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Choose a new password">
      {!token ? (
        <Notice tone="loss">This page needs the link from your reset e-mail.</Notice>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <Field label="New password" error={error}>
            {(id) => <input id={id} className="field-input" type="password" autoComplete="new-password" value={password}
              onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          <Button type="submit" className="w-full" busy={busy}>
            Save new password
          </Button>
        </form>
      )}
    </AuthShell>
  );
}
