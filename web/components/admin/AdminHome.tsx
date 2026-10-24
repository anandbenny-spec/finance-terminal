"use client";

// /admin (C6; Spec 01 §4.4): Supabase Auth login for the one admin (sign-ups are off; the admin was created by hand
// with app_metadata.role = "admin"), then the admin's links and a way to log out. Showing the admin's page is display
// only: every /api/admin/* route verifies the token itself (A3). A signed-in account that isn't the admin is signed
// out again at once. Errors are one plain sentence: never Auth's raw message.
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { useAdmin } from "@/components/shell/admin";
import { publicConfig } from "@/lib/supabase/public";

const INPUT =
  "h-9 w-full rounded-md border border-line-input bg-raised px-2.5 text-ui text-ink-1 placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-amber";

export function LoginForm({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { browserClient, isAdminUser } = await import("@/lib/supabase/browser");
    const db = browserClient();
    if (!db) {
      setBusy(false);
      setError("Sign-in isn't set up on this deployment.");
      return;
    }
    const { data, error: authError } = await db.auth.signInWithPassword({ email, password });
    if (authError || !data.user) {
      setBusy(false);
      setError("Couldn't log in. Check the email and password.");
      return;
    }
    if (!isAdminUser(data.user)) {
      await db.auth.signOut();
      setBusy(false);
      setError("This account isn't the admin.");
      return;
    }
    setPassword("");
    setBusy(false);
    onSignedIn();
  }

  return (
    <form onSubmit={submit} className="flex max-w-80 flex-col gap-3" aria-describedby={error ? "login-error" : undefined}>
      <label className="flex flex-col gap-1 text-meta text-ink-2">
        Email
        <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT} />
      </label>
      <label className="flex flex-col gap-1 text-meta text-ink-2">
        Password
        <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT} />
      </label>
      <button
        type="submit"
        disabled={busy}
        className="h-9 rounded-md bg-primary px-3 text-ui font-medium text-primary-foreground hover:bg-primary/80 disabled:opacity-50"
      >
        {busy ? "Logging in…" : "Log in"}
      </button>
      {error && (
        <p id="login-error" role="alert">
          <StateNote kind="error">{error}</StateNote>
        </p>
      )}
    </form>
  );
}

export function AdminHome() {
  const { isAdmin, ready, recheck } = useAdmin();
  const [email, setEmail] = useState<string | null>(null);

  async function signOut() {
    const { browserClient } = await import("@/lib/supabase/browser");
    await browserClient()?.auth.signOut();
    setEmail(null);
    recheck();
  }

  async function signedIn() {
    const { browserClient } = await import("@/lib/supabase/browser");
    const { data } = (await browserClient()?.auth.getSession()) ?? { data: null };
    setEmail(data?.session?.user.email ?? null);
    recheck();
  }

  let body;
  if (!publicConfig()) body = <StateNote kind="unavailable">Sign-in isn&apos;t set up on this deployment.</StateNote>;
  else if (!ready) body = <StateNote kind="empty">Checking your session…</StateNote>;
  else if (!isAdmin) body = <LoginForm onSignedIn={signedIn} />;
  else
    body = (
      <div className="flex flex-col gap-3">
        <p className="text-ink-2">Logged in as the admin{email ? ` (${email})` : ""}.</p>
        <ul className="flex flex-col gap-1">
          <li>
            <Link href="/admin/health" className="text-ink-1 underline">
              Health
            </Link>
            <span className="text-ink-2">: the tasks, open alerts and recent runs.</span>
          </li>
          <li>
            <Link href="/admin/labelling" className="text-ink-1 underline">
              Labelling
            </Link>
            <span className="text-ink-2">: Pass 2&apos;s evaluation sets, one item at a time.</span>
          </li>
          <li className="text-ink-2">Refresh: the refresh button in each Fed vs RBI panel&apos;s header.</li>
        </ul>
        <div>
          <button type="button" onClick={signOut} className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active">
            Log out
          </button>
        </div>
      </div>
    );

  return (
    <PanelGrid>
      <Panel title={isAdmin ? "Admin" : "Log in"} titleId="panel-admin" span={6} subtitle="For the site's one admin. Visitors don't need an account.">
        {body}
      </Panel>
    </PanelGrid>
  );
}
