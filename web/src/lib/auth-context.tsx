"use client";

import {
  createContext,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";
import { authStore } from "@/lib/auth-store";
import { isStaffRole } from "@/lib/roles";

interface AuthContextValue {
  user: { email: string; name: string; role: string } | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
  isAdmin: boolean;
  /** support or admin: may review approvals routed to staff. */
  isStaff: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const state = useSyncExternalStore(
    authStore.subscribe,
    authStore.getSnapshot,
    authStore.getServerSnapshot,
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login: authStore.login,
      signup: authStore.signup,
      logout: authStore.logout,
      isAuthenticated: !!state.user,
      isAdmin: state.user?.role === "admin",
      isStaff: isStaffRole(state.user?.role),
    }),
    [state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
