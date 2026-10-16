"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { AuthResponse, Profile, User } from "@/types/api";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (email: string, password: string, fullName: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  setProfile: (p: Profile) => void;
}

const Ctx = React.createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null);
  const [loading, setLoading] = React.useState(true);
  const router = useRouter();

  const refreshUser = React.useCallback(async () => {
    try {
      setUser(await api<User>("/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        // access token may have expired: try the refresh cookie once
        try {
          const r = await api<AuthResponse>("/auth/refresh", { method: "POST", noRefresh: true });
          setUser(r.user);
        } catch {
          setUser(null);
        }
      } else setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { void refreshUser(); }, [refreshUser]);

  const value: AuthState = {
    user,
    loading,
    async login(email, password) {
      const r = await api<AuthResponse>("/auth/login", { body: { email, password } });
      setUser(r.user);
      return r.user;
    },
    async register(email, password, fullName) {
      const r = await api<AuthResponse>("/auth/register", { body: { email, password, full_name: fullName } });
      setUser(r.user);
      return r.user;
    },
    async logout() {
      try { await api("/auth/logout", { method: "POST" }); } catch { /* already signed out */ }
      setUser(null);
      router.push("/login");
    },
    refreshUser,
    setProfile(p) { setUser((u) => (u ? { ...u, profile: p } : u)); },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = React.useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}
