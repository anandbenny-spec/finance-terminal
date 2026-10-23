"use client";

// Whether the logged-in admin is looking (Spec 01 §4.1: the sidebar's admin group; Design 01 §4.1: the top bar's
// badge). Display only: the API decides what a session may do (lib/supabase/browser.ts). For a visitor with no
// stored session, supabase-js isn't even loaded. C6's login calls `recheck()` after signing in.
import { ShieldCheck } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type AdminState = { isAdmin: boolean; recheck: () => void };

const AdminContext = createContext<AdminState>({ isAdmin: false, recheck: () => {} });

export function AdminProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false);
  const [checks, setChecks] = useState(0);
  const recheck = useCallback(() => setChecks((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    let unsubscribe: (() => void) | undefined;
    import("@/lib/supabase/browser").then(async ({ browserClient, hasStoredSession, isAdminUser }) => {
      if (!alive || !hasStoredSession()) return;
      const db = browserClient();
      if (!db) return;
      const { data } = await db.auth.getSession();
      if (!alive) return;
      setIsAdmin(isAdminUser(data.session?.user));
      const { data: sub } = db.auth.onAuthStateChange((_event, session) => setIsAdmin(isAdminUser(session?.user)));
      unsubscribe = () => sub.subscription.unsubscribe();
    });
    return () => {
      alive = false;
      unsubscribe?.();
    };
  }, [checks]);

  const value = useMemo(() => ({ isAdmin, recheck }), [isAdmin, recheck]);
  return <AdminContext.Provider value={value}>{children}</AdminContext.Provider>;
}

export function useAdmin(): AdminState {
  return useContext(AdminContext);
}

/** The top bar's badge, for the admin only. */
export function AdminBadge() {
  const { isAdmin } = useAdmin();
  if (!isAdmin) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-line-strong px-1.5 text-meta text-ink-2">
      <ShieldCheck aria-hidden className="size-3.5 text-ink-3" />
      Admin
    </span>
  );
}
