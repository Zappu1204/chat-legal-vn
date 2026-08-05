"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import type { UserPublic } from "@/lib/types";

/** Redirects to /login when unauthenticated. Refreshes the current user. */
export function AuthGuard({
  children,
  requireAdmin = false,
}: {
  children: React.ReactNode;
  requireAdmin?: boolean;
}) {
  const router = useRouter();
  const { accessToken, user, setUser, logout } = useAuthStore();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;
    if (!accessToken) {
      router.replace("/login");
      return;
    }
    api
      .me()
      .then((u) => {
        if (!active) return;
        setUser(u as UserPublic);
        setChecking(false);
      })
      .catch(() => {
        logout();
        router.replace("/login");
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        Đang tải…
      </div>
    );
  }

  if (requireAdmin && user?.role !== "admin") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-slate-600">
        <p className="text-lg font-semibold">Bạn không có quyền truy cập trang này.</p>
        <button
          onClick={() => router.replace("/chat")}
          className="cursor-pointer rounded-md bg-brand-600 px-4 py-2 text-white transition-colors duration-200 hover:bg-brand-700"
        >
          Về trang trò chuyện
        </button>
      </div>
    );
  }

  return <>{children}</>;
}
