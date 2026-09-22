import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchMe, logout as apiLogout } from "../api/auth";
import { setUnauthorizedHandler, storeToken, storedToken } from "../api/client";
import type { Me } from "../api/auth";

interface AuthState {
  user: Me | null;
  /** True until the stored token has been checked. Rendering a signed-out
   *  screen during this window makes a returning user see a sign-in form for
   *  a moment on every load, which reads as being logged out. */
  loading: boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthState>({
  user: null,
  loading: true,
  signIn: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  const clear = useCallback(() => {
    setUser(null);
    // Every cached response was fetched as somebody. Leaving them would show
    // one account's portfolio to the next person to sign in on this browser.
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setUnauthorizedHandler(clear);
    return () => setUnauthorizedHandler(null);
  }, [clear]);

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!storedToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await fetchMe();
        if (!cancelled) setUser(me);
      } catch {
        // The interceptor has already cleared the token on a 401. Anything
        // else (API down) leaves it alone so a reload can recover.
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    restore();
    return () => { cancelled = true; };
  }, []);

  const signIn = useCallback(async (token: string) => {
    storeToken(token);
    queryClient.clear();
    setUser(await fetchMe());
  }, [queryClient]);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // A failed revoke must not strand somebody signed in locally. The token
      // is dropped either way; the worst case is a server-side row that
      // expires on its own.
    }
    storeToken(null);
    clear();
  }, [clear]);

  const value = useMemo(
    () => ({ user, loading, signIn, signOut }),
    [user, loading, signIn, signOut],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  return useContext(Ctx);
}
