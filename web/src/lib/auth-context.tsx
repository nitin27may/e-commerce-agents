"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "@/lib/api";

interface User {
  email: string;
  name: string;
  role: string;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  signup: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const STORAGE_KEY_USER = "ecommerce_user";
const STORAGE_KEY_ACCESS = "ecommerce_access_token";
const STORAGE_KEY_REFRESH = "ecommerce_refresh_token";

function persistAuth(
  user: User,
  accessToken: string,
  refreshToken: string
): void {
  localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user));
  localStorage.setItem(STORAGE_KEY_ACCESS, accessToken);
  localStorage.setItem(STORAGE_KEY_REFRESH, refreshToken);
  api.setToken(accessToken);
  api.setRefreshToken(refreshToken);
}

function clearAuth(): void {
  localStorage.removeItem(STORAGE_KEY_USER);
  localStorage.removeItem(STORAGE_KEY_ACCESS);
  localStorage.removeItem(STORAGE_KEY_REFRESH);
  api.setToken(null);
  api.setRefreshToken(null);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    isLoading: true,
  });

  // Restore session from localStorage on mount
  useEffect(() => {
    try {
      const storedUser = localStorage.getItem(STORAGE_KEY_USER);
      const storedToken = localStorage.getItem(STORAGE_KEY_ACCESS);

      if (storedUser && storedToken) {
        const user = JSON.parse(storedUser) as User;
        const storedRefresh = localStorage.getItem(STORAGE_KEY_REFRESH);
        api.setToken(storedToken);
        api.setRefreshToken(storedRefresh);
        setState({ user, isLoading: false });
      } else {
        setState({ user: null, isLoading: false });
      }
    } catch {
      clearAuth();
      setState({ user: null, isLoading: false });
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    persistAuth(result.user, result.access_token, result.refresh_token);
    setState({ user: result.user, isLoading: false });
  }, []);

  const signup = useCallback(
    async (name: string, email: string, password: string) => {
      const result = await api.signup(email, password, name);
      persistAuth(result.user, result.access_token, result.refresh_token);
      setState({ user: result.user, isLoading: false });
    },
    []
  );

  const logout = useCallback(() => {
    clearAuth();
    setState({ user: null, isLoading: false });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      signup,
      logout,
      isAuthenticated: !!state.user,
      isAdmin: state.user?.role === "admin",
    }),
    [state, login, signup, logout]
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
