"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NexusUnreachableError } from "./api";

export type PollState<T> = {
  data: T | null;
  /** Human-readable error message, if the last fetch failed. */
  error: string | null;
  /** True when the control plane could not be reached at all. */
  unreachable: boolean;
  /** True until the first fetch settles. */
  loading: boolean;
  lastUpdated: Date | null;
  /** Trigger an immediate refresh outside the polling interval. */
  refresh: () => void;
};

/**
 * Poll an async fetcher on an interval (default 5s). Errors never throw out
 * of the hook; they surface via `error` / `unreachable` while the last good
 * `data` is kept so the UI can degrade gracefully.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs = 5000,
  deps: readonly unknown[] = [],
): PollState<T> {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let active = true;

    const tick = async () => {
      try {
        const result = await fetcherRef.current();
        if (!active) return;
        setData(result);
        setError(null);
        setUnreachable(false);
        setLastUpdated(new Date());
      } catch (err) {
        if (!active) return;
        if (err instanceof NexusUnreachableError) {
          setUnreachable(true);
          setError(err.message);
        } else {
          setUnreachable(false);
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    void tick();
    const timer = setInterval(() => void tick(), intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, nonce, ...deps]);

  return { data, error, unreachable, loading, lastUpdated, refresh };
}
