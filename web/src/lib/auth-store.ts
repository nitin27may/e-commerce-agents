import { api } from "@/lib/api";

/**
 * Session store behind `useAuth()`, shaped for `useSyncExternalStore`.
 *
 * localStorage holds the session (user JSON + access + refresh token); this
 * module owns reading it and keeping `api` in step. Server rendering has no
 * storage, so `getServerSnapshot()` is a fixed "still loading, nobody signed in"
 * value. React renders that during hydration and then switches to
 * `getSnapshot()`, which is what keeps the first client render equal to the
 * server HTML without a mount effect calling setState.
 */

export interface AuthUser {
  email: string;
  name: string;
  role: string;
}

export interface AuthState {
  user: AuthUser | null;
  isLoading: boolean;
}

const STORAGE_KEY_USER = "ecommerce_user";
const STORAGE_KEY_ACCESS = "ecommerce_access_token";
const STORAGE_KEY_REFRESH = "ecommerce_refresh_token";

export const SERVER_AUTH_STATE: AuthState = Object.freeze({ user: null, isLoading: true });

let current: AuthState | null = null;
const listeners = new Set<() => void>();

function emit(next: AuthState): void {
  current = next;
  listeners.forEach((l) => l());
}

function clearStorage(): void {
  try {
    localStorage.removeItem(STORAGE_KEY_USER);
    localStorage.removeItem(STORAGE_KEY_ACCESS);
    localStorage.removeItem(STORAGE_KEY_REFRESH);
  } catch {
    /* storage blocked: the in-memory copy is all there is */
  }
  api.setToken(null);
  api.setRefreshToken(null);
}

/** Restore the session from localStorage and hand its tokens to the API client. */
function readSession(): AuthState {
  try {
    const storedUser = localStorage.getItem(STORAGE_KEY_USER);
    const storedToken = localStorage.getItem(STORAGE_KEY_ACCESS);
    if (storedUser && storedToken) {
      const user = JSON.parse(storedUser) as AuthUser;
      api.setToken(storedToken);
      api.setRefreshToken(localStorage.getItem(STORAGE_KEY_REFRESH));
      return { user, isLoading: false };
    }
  } catch {
    clearStorage();
  }
  return { user: null, isLoading: false };
}

function persist(user: AuthUser, accessToken: string, refreshToken: string): void {
  localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user));
  localStorage.setItem(STORAGE_KEY_ACCESS, accessToken);
  localStorage.setItem(STORAGE_KEY_REFRESH, refreshToken);
  api.setToken(accessToken);
  api.setRefreshToken(refreshToken);
  emit({ user, isLoading: false });
}

export const authStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  /** Client snapshot. Stable between changes, as useSyncExternalStore requires. */
  getSnapshot(): AuthState {
    current ??= readSession();
    return current;
  },

  getServerSnapshot(): AuthState {
    return SERVER_AUTH_STATE;
  },

  async login(email: string, password: string): Promise<void> {
    const result = await api.login(email, password);
    persist(result.user, result.access_token, result.refresh_token);
  },

  async signup(name: string, email: string, password: string): Promise<void> {
    const result = await api.signup(email, password, name);
    persist(result.user, result.access_token, result.refresh_token);
  },

  logout(): void {
    clearStorage();
    emit({ user: null, isLoading: false });
  },

  /** Forget the cached session so the next read goes back to localStorage. Tests only. */
  resetForTests(): void {
    current = null;
    listeners.clear();
  },
};
