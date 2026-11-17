"use client";

// Plain search for the command bar (K-07, #585; Spec 07 §4 step 1): `api.search_index` from the browser with the browser key,
// debounced, the previous request aborted, each page checked against its exact count (lib/cmdbar.ts `searchIndex`), and
// "Show more" for the next page. The state is only ever set from the request's own callbacks, and a result is used only
// while its term is still the box's term, so a slow answer to an old term never shows.
import { useCallback, useEffect, useRef, useState } from "react";
import { istToday, searchConfig, searchIndex, searchTerm, type SearchRow } from "@/lib/cmdbar";

export type SearchState = {
  /** The term these rows are for. */
  term: string;
  rows: SearchRow[];
  total: number;
  status: "ok" | "error";
  /** A next page is being read. */
  more: boolean;
};

const DEBOUNCE_MS = 150;

export function useSearch(input: string): { term: string | null; result: SearchState | null; loading: boolean; loadMore: () => void } {
  const term = searchTerm(input);
  const [result, setResult] = useState<SearchState | null>(null);
  const moreAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!term) return;
    const config = searchConfig();
    const ac = new AbortController();
    const timer = setTimeout(() => {
      if (!config) {
        // No Data API in this build (CI's build, a fresh clone): the site's own pages still match; nothing else is listed.
        setResult({ term, rows: [], total: 0, status: "ok", more: false });
        return;
      }
      searchIndex(term, 0, { ...config, today: istToday(), signal: ac.signal })
        .then((page) => setResult({ term, rows: page.rows, total: page.total, status: "ok", more: false }))
        .catch(() => {
          if (!ac.signal.aborted) setResult({ term, rows: [], total: 0, status: "error", more: false });
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ac.abort();
      moreAbort.current?.abort();
    };
  }, [term]);

  const loadMore = useCallback(() => {
    const config = searchConfig();
    if (!config || !result || result.status !== "ok" || result.more || result.rows.length >= result.total) return;
    const ac = new AbortController();
    moreAbort.current = ac;
    const { term: t, rows, total } = result;
    setResult({ ...result, more: true });
    searchIndex(t, rows.length, { ...config, today: istToday(), signal: ac.signal })
      .then((page) => {
        // The count moved between pages: the list would no longer be one consistent read, so it is an error, not a guess.
        if (page.total !== total) throw new Error("count changed");
        setResult((r) => (r && r.term === t ? { ...r, rows: [...rows, ...page.rows], more: false } : r));
      })
      .catch(() => {
        if (!ac.signal.aborted) setResult((r) => (r && r.term === t ? { ...r, rows: [], total: 0, status: "error", more: false } : r));
      });
  }, [result]);

  const current = term && result?.term === term ? result : null;
  return { term, result: current, loading: term !== null && current === null, loadMore };
}
