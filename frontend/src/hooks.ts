import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

/** Runs `fn` on mount and whenever `deps` change; ignores stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: (data: T | undefined) => number | null): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const pollRef = useRef(pollMs);
  pollRef.current = pollMs;

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async (first: boolean) => {
      if (first) setLoading(true);
      try {
        const d = await fnRef.current();
        if (!live) return;
        setData(d);
        setError(undefined);
        const next = pollRef.current?.(d) ?? null;
        if (next) timer = setTimeout(() => void run(false), next);
      } catch (e) {
        if (!live) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (live && first) setLoading(false);
      }
    };
    void run(true);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
