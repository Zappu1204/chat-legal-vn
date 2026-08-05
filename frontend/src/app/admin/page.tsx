"use client";

import { ArrowLeft, SlidersHorizontal, Users, Database } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { AdminConfig } from "@/components/admin/AdminConfig";
import { AdminUsers } from "@/components/admin/AdminUsers";
import { AdminPipeline } from "@/components/admin/AdminPipeline";
import { cn } from "@/lib/utils";

type Tab = "config" | "users" | "pipeline";

const TABS: { id: Tab; label: string; icon: typeof Users }[] = [
  { id: "config", label: "Cấu hình hệ thống", icon: SlidersHorizontal },
  { id: "users", label: "Người dùng", icon: Users },
  { id: "pipeline", label: "Lập chỉ mục dữ liệu", icon: Database },
];

function AdminView() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("config");

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-4">
          <button
            onClick={() => router.push("/chat")}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-slate-600 transition-colors duration-200 hover:bg-slate-100"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Về trò chuyện
          </button>
          <h1 className="font-serif text-xl font-semibold text-slate-900">
            Bảng điều khiển quản trị
          </h1>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-6 flex gap-1 border-b border-slate-200">
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors duration-200",
                  tab === t.id
                    ? "border-brand-600 text-brand-700"
                    : "border-transparent text-slate-500 hover:text-slate-800",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {t.label}
              </button>
            );
          })}
        </div>

        {tab === "config" && <AdminConfig />}
        {tab === "users" && <AdminUsers />}
        {tab === "pipeline" && <AdminPipeline />}
      </div>
    </div>
  );
}

export default function AdminPage() {
  return (
    <AuthGuard requireAdmin>
      <AdminView />
    </AuthGuard>
  );
}
