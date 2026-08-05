"use client";

import { useAuthStore } from "./auth-store";

const API_PREFIX = "/api/v1";

/** Refresh-once-on-401 wrapper around fetch with bearer auth. */
async function authFetch(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const { accessToken, refreshToken, setTokens, logout } =
    useAuthStore.getState();

  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_PREFIX}${path}`, { ...init, headers });

  if (res.status === 401 && retry && refreshToken) {
    const r = await fetch(`${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (r.ok) {
      const tokens = await r.json();
      setTokens(tokens.access_token, tokens.refresh_token);
      return authFetch(path, init, false);
    }
    logout();
  }
  return res;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // ---- auth ----
  async register(email: string, password: string, fullName?: string) {
    const res = await fetch(`${API_PREFIX}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name: fullName }),
    });
    return jsonOrThrow<{ access_token: string; refresh_token: string }>(res);
  },
  async login(email: string, password: string) {
    const res = await fetch(`${API_PREFIX}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    return jsonOrThrow<{ access_token: string; refresh_token: string }>(res);
  },
  async me() {
    return jsonOrThrow(await authFetch("/auth/me"));
  },

  // ---- conversations ----
  async listConversations() {
    return jsonOrThrow(await authFetch("/conversations"));
  },
  async getConversation(id: string) {
    return jsonOrThrow(await authFetch(`/conversations/${id}`));
  },
  async deleteConversation(id: string) {
    const res = await authFetch(`/conversations/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) throw new Error("Xoá thất bại");
  },

  // ---- admin ----
  async getConfig() {
    return jsonOrThrow(await authFetch("/admin/config"));
  },
  async updateConfig(patch: Record<string, unknown>) {
    return jsonOrThrow(
      await authFetch("/admin/config", {
        method: "PUT",
        body: JSON.stringify(patch),
      }),
    );
  },
  async listUsers() {
    return jsonOrThrow(await authFetch("/admin/users"));
  },
  async setUserRole(userId: string, role: "user" | "admin") {
    return jsonOrThrow(
      await authFetch(`/admin/users/${userId}/role`, {
        method: "PUT",
        body: JSON.stringify({ role }),
      }),
    );
  },
  async triggerIngest(limit = 0) {
    return jsonOrThrow(
      await authFetch(`/admin/pipeline/ingest?limit=${limit}`, {
        method: "POST",
      }),
    );
  },
  async listJobs() {
    return jsonOrThrow(await authFetch("/admin/pipeline/jobs"));
  },
};

export { authFetch };
