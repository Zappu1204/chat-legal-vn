"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck, Shield } from "lucide-react";
import { api } from "@/lib/api";
import type { UserPublic } from "@/lib/types";

export function AdminUsers() {
  const qc = useQueryClient();
  const { data: users = [], isLoading } = useQuery<UserPublic[]>({
    queryKey: ["admin-users"],
    queryFn: () => api.listUsers() as Promise<UserPublic[]>,
  });

  const mutate = useMutation({
    mutationFn: ({ id, role }: { id: string; role: "user" | "admin" }) =>
      api.setUserRole(id, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Đang tải người dùng…
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Email</th>
            <th className="px-4 py-3">Họ tên</th>
            <th className="px-4 py-3">Vai trò</th>
            <th className="px-4 py-3">Trạng thái</th>
            <th className="px-4 py-3 text-right">Thao tác</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {users.map((u) => (
            <tr key={u.id} className="transition-colors duration-200 hover:bg-slate-50">
              <td className="px-4 py-3 font-medium text-slate-900">{u.email}</td>
              <td className="px-4 py-3 text-slate-600">{u.full_name ?? "—"}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                    u.role === "admin"
                      ? "bg-brand-50 text-brand-700"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {u.role === "admin" ? (
                    <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                  ) : (
                    <Shield className="h-3 w-3" aria-hidden="true" />
                  )}
                  {u.role}
                </span>
              </td>
              <td className="px-4 py-3">
                <span
                  className={
                    u.is_active ? "text-emerald-600" : "text-rose-600"
                  }
                >
                  {u.is_active ? "Hoạt động" : "Đã khoá"}
                </span>
              </td>
              <td className="px-4 py-3 text-right">
                <button
                  onClick={() =>
                    mutate.mutate({
                      id: u.id,
                      role: u.role === "admin" ? "user" : "admin",
                    })
                  }
                  className="cursor-pointer rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-100"
                >
                  {u.role === "admin" ? "Hạ xuống User" : "Nâng lên Admin"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
