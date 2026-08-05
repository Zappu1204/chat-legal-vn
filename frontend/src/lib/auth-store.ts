"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { UserPublic } from "./types";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: UserPublic | null;
  setTokens: (access: string, refresh: string) => void;
  setUser: (user: UserPublic | null) => void;
  logout: () => void;
}

// Client state (tokens + current user). Persisted to localStorage so a reload
// keeps the session. Server state (conversations, admin data) is handled by
// React Query instead.
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      setTokens: (access, refresh) =>
        set({ accessToken: access, refreshToken: refresh }),
      setUser: (user) => set({ user }),
      logout: () =>
        set({ accessToken: null, refreshToken: null, user: null }),
    }),
    { name: "clvn-auth" },
  ),
);
