"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Loads data on mount and re-loads it on an interval (faster while `fast` is true, e.g. while
 * something is still processing). `reload()` forces an immediate refresh.
 */
export function usePoll<T>(load: () => Promise<T>, { intervalMs = 15_000, fastMs = 2_500, fast = false } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), fast ? fastMs : intervalMs);
    return () => clearInterval(timer);
  }, [reload, fast, fastMs, intervalMs]);

  return { data, error, loading, reload };
}
