import { useState } from "react";
import { Link } from "react-router-dom";

import { useSession } from "../lib/session";

const DEMO = import.meta.env.VITE_DEMO === "true";
const DEMO_PASSWORD = (import.meta.env.VITE_DEMO_PASSWORD as string | undefined) ?? "";
const DEMO_USERS: { label: string; email: string }[] = [
  { label: "Shift manager", email: "shift@restro.test" },
  { label: "Manager", email: "manager@restro.test" },
  { label: "Owner", email: "owner@restro.test" },
];

export function Login() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function go(e?: React.FormEvent, as?: { email: string }) {
    e?.preventDefault();
    setBusy(true);
    setError(await signIn(as?.email ?? email.trim(), as ? DEMO_PASSWORD : password));
    setBusy(false);
  }

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={go}>
        <Link to="/" className="brand"><img src="/favicon.svg" alt="" width="28" height="28" />Mehmanawazi</Link>
        <h1>Sign in to the console</h1>
        <label className="field">
          Email
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          Password
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="error-text" role="alert">{error}</div>}
        <button className="btn primary big" disabled={busy}>Sign in</button>
        {DEMO && DEMO_PASSWORD && (
          <>
            <div className="hint">Demo build · sign in as</div>
            <div className="demo-roles">
              {DEMO_USERS.map((u) => (
                <button key={u.email} type="button" className="btn small" disabled={busy} onClick={() => void go(undefined, u)}>
                  {u.label}
                </button>
              ))}
            </div>
          </>
        )}
      </form>
    </div>
  );
}
