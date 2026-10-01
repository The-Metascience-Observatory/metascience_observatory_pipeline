import { useEffect } from "react";

// Call `fn` now and then every `ms`, until the component unmounts or `fn`
// changes (wrap it in useCallback so it changes only when its inputs do).
export function usePolling(fn: () => unknown, ms: number) {
  useEffect(() => {
    fn();
    const t = setInterval(fn, ms);
    return () => clearInterval(t);
  }, [fn, ms]);
}
