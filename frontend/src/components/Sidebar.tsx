"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquarePlus,
  MessageSquare,
  Trash2,
  Settings,
  LogOut,
  Scale,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import type { ConversationPublic } from "@/lib/types";
import { cn } from "@/lib/utils";

export function Sidebar({
  activeId,
  onSelect,
  onNew,
}: {
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const { user, logout } = useAuthStore();

  const { data: conversations = [] } = useQuery<ConversationPublic[]>({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations() as Promise<ConversationPublic[]>,
  });

  async function remove(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await api.deleteConversation(id);
    qc.invalidateQueries({ queryKey: ["conversations"] });
    if (activeId === id) onNew();
  }

  return (
    <aside className="flex h-screen w-72 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-4">
        <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
          <Scale className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="font-serif text-lg font-semibold text-slate-900">
          Chat Legal VN
        </span>
      </div>

      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white transition-colors duration-200 hover:bg-brand-700"
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
          Cuộc trò chuyện mới
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2" aria-label="Lịch sử trò chuyện">
        {conversations.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-slate-400">
            Chưa có cuộc trò chuyện nào.
          </p>
        )}
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={cn(
              "group mb-1 flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors duration-200",
              activeId === c.id
                ? "bg-brand-50 text-brand-800"
                : "text-slate-700 hover:bg-slate-100",
            )}
          >
            <MessageSquare className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
            <span className="flex-1 truncate">{c.title}</span>
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => remove(c.id, e)}
              className="invisible cursor-pointer rounded p-1 text-slate-400 transition-colors duration-200 hover:bg-rose-50 hover:text-rose-600 group-hover:visible"
              aria-label="Xoá cuộc trò chuyện"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
          </button>
        ))}
      </nav>

      <div className="border-t border-slate-200 p-3">
        <div className="mb-2 px-1 text-xs text-slate-500">
          <p className="truncate font-medium text-slate-700">{user?.email}</p>
          <p className="capitalize">{user?.role}</p>
        </div>
        {user?.role === "admin" && (
          <button
            onClick={() => router.push("/admin")}
            className="mb-1 flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-700 transition-colors duration-200 hover:bg-slate-100"
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
            Quản trị hệ thống
          </button>
        )}
        <button
          onClick={() => {
            logout();
            router.replace("/login");
          }}
          className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-700 transition-colors duration-200 hover:bg-slate-100"
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Đăng xuất
        </button>
      </div>
    </aside>
  );
}
