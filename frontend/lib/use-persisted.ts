"use client";

// localStorage-backed useState: restores once after mount (never during render —
// localStorage doesn't exist during SSR/prerender, and reading it in a render
// would break hydration), then mirrors every change back. Survives refresh, new
// tabs, and browser restarts. Same-origin only; NOT namespaced per tenant, so a
// shared browser carries one user's UI picks to the next — fine for a convenience
// selector, don't store anything trust-sensitive here.
import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

export function usePersisted<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(initial);
  // Skip the mount write so `initial` never clobbers the stored value before the
  // restore effect (which runs first, being declared first) sets it.
  const skipFirstWrite = useRef(true);

  useEffect(() => {
    const raw = window.localStorage.getItem(key);

    if (raw !== null) {
      try {
        setValue(JSON.parse(raw) as T);
      } catch {
        // corrupt entry — keep the initial value
      }
    }
  }, [key]);

  useEffect(() => {
    if (skipFirstWrite.current) {
      skipFirstWrite.current = false;

      return;
    }
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);

  return [value, setValue];
}
