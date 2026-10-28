"use client";

// Whether the logged-in admin is looking (Spec 01 §4.1: the sidebar's admin group; Design 01 §4.1: the top bar's
// badge). Display only: the API decides what a session may do (lib/supabase/browser.ts). For a visitor with no
// stored session, supabase-js isn't even loaded. C6's login calls `recheck()` after signing in.
import { ShieldCheck } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/** `ready`: the session has been checked (so a page can tell "not the admin" from "not known yet"). */
type AdminState = { isAdmin: boolean; ready: boolean; recheck: () => void };

const AdminContext = createContext<AdminState>({ isAdmin: false, ready: false, recheck: () => {} });

export function AdminProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false);
  const [ready, setReady] = useState(false);
  const [checks, setChecks] = useState(0);
  const recheck = useCallback(() => setChecks((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    let unsubscribe: (() => void) | undefined;
    import("@/lib/supabase/browser").then(async ({ browserClient, hasStoredSession, isAdminUser }) => {
      if (!alive) return;
      const db = hasStoredSession() ? browserClient() : null;
      if (!db) {
        setIsAdmin(false);
        setReady(true);
        return;
      }
      const { data } = await db.auth.getSession();
      if (!alive) return;
      setIsAdmin(isAdminUser(data.session?.user));
      setReady(true);
      const { data: sub } = db.auth.onAuthStateChange((_event, session) => setIsAdmin(isAdminUser(session?.user)));
      unsubscribe = () => sub.subscription.unsubscribe();
    });
    return () => {
      alive = false;
      unsubscribe?.();
    };
  }, [checks]);

  const value = useMemo(() => ({ isAdmin, ready, recheck }), [isAdmin, ready, recheck]);
  return <AdminContext.Provider value={value}>{children}</AdminContext.Provider>;
}

export function useAdmin(): AdminState {
  return useContext(AdminContext);
}

/** The badge itself. Below `sm` it shows the icon alone and keeps the word for screen readers: with the labelled Menu
 *  button (#147) the top bar has no room for the word at 320 px (checked with Plex: 347 px of content in 320). */
export function AdminBadgeView() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-line-strong px-1.5 text-meta text-ink-2">
      <ShieldCheck aria-hidden className="size-3.5 text-ink-3" />
      <span className="max-sm:sr-only">Admin</span>
    </span>
  );
}

/** The top bar's badge, for the admin only. */
export function AdminBadge() {
  const { isAdmin } = useAdmin();
  return isAdmin ? <AdminBadgeView /> : null;
}
