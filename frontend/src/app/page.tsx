"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuthStore } from "@/lib/auth-store";

export default function Home() {
  const router = useRouter();
  const accessToken = useAuthStore((s) => s.accessToken);
  useEffect(() => {
    router.replace(accessToken ? "/chat" : "/login");
  }, [accessToken, router]);
  return (
    <div className="flex min-h-screen items-center justify-center text-slate-500">
      Đang chuyển hướng…
    </div>
  );
}
