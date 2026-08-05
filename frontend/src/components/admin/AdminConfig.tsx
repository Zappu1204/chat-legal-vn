"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Save, CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SystemConfigRead } from "@/lib/types";

// Tunable fields grouped for the editor. Secrets are write-only (password
// inputs) and only sent when the admin types a new value.
const NUMERIC_FIELDS: { key: string; label: string; step?: number }[] = [
  { key: "llm_temperature", label: "LLM temperature", step: 0.1 },
  { key: "llm_max_tokens", label: "LLM max tokens" },
  { key: "llm_max_concurrency", label: "LLM max concurrency" },
  { key: "retriever_top_k", label: "Retriever top-k" },
  { key: "reranker_top_k", label: "Reranker top-k" },
  { key: "reranker_threshold", label: "Reranker threshold", step: 0.05 },
  { key: "hybrid_rrf_k", label: "Hybrid RRF k" },
  { key: "hybrid_alpha_dense", label: "Alpha dense", step: 0.05 },
  { key: "hybrid_alpha_sparse", label: "Alpha sparse", step: 0.05 },
  { key: "diversity_max_per_parent", label: "Diversity / parent" },
  { key: "query_hyde_num", label: "HyDE passages" },
  { key: "graph_expand_hops", label: "Graph hops" },
  { key: "graph_expand_limit", label: "Graph limit" },
];

const BOOL_FIELDS: { key: string; label: string }[] = [
  { key: "query_decompose_enabled", label: "Phân rã câu hỏi" },
  { key: "query_hyde_enabled", label: "Bật HyDE" },
  { key: "graph_expand_enabled", label: "Mở rộng GraphRAG" },
];

const TEXT_FIELDS: { key: string; label: string }[] = [
  { key: "llm_base_url", label: "LLM base URL" },
  { key: "llm_model_name", label: "LLM model" },
  { key: "embedding_base_url", label: "Embedding base URL" },
  { key: "reranker_base_url", label: "Reranker base URL" },
];

const SECRET_FIELDS: { key: string; label: string }[] = [
  { key: "llm_api_key", label: "LLM API key" },
  { key: "embedding_api_key", label: "Embedding API key" },
  { key: "reranker_api_key", label: "Reranker API key" },
];

export function AdminConfig() {
  const { data, isLoading, refetch } = useQuery<SystemConfigRead>({
    queryKey: ["admin-config"],
    queryFn: () => api.getConfig() as Promise<SystemConfigRead>,
  });

  const [form, setForm] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) setForm({ ...data.values });
  }, [data]);

  if (isLoading || !data) {
    return (
      <div className="flex items-center gap-2 text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Đang tải cấu hình…
      </div>
    );
  }

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      // Only send changed numeric/bool/text fields + any typed secrets.
      const patch: Record<string, unknown> = { ...form, ...secrets };
      await api.updateConfig(patch);
      setSecrets({});
      setSaved(true);
      await refetch();
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  }

  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-slate-900">Tham số truy hồi & sinh</h2>
            <p className="text-sm text-slate-500">
              Phiên bản hiện tại: <span className="font-medium">#{data.revision}</span>
              {data.updated_by ? ` · cập nhật bởi ${data.updated_by}` : ""}
            </p>
          </div>
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition-colors duration-200 hover:bg-brand-700 disabled:opacity-60"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : saved ? (
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" aria-hidden="true" />
            )}
            {saved ? "Đã áp dụng (hot-reload)" : "Lưu & áp dụng"}
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {TEXT_FIELDS.map((f) => (
            <label key={f.key} className="block">
              <span className="mb-1 block text-sm font-medium text-slate-700">
                {f.label}
              </span>
              <input
                type="text"
                value={(form[f.key] as string) ?? ""}
                onChange={(e) => set(f.key, e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition-colors duration-200 focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
            </label>
          ))}
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {NUMERIC_FIELDS.map((f) => (
            <label key={f.key} className="block">
              <span className="mb-1 block text-sm font-medium text-slate-700">
                {f.label}
              </span>
              <input
                type="number"
                step={f.step ?? 1}
                value={(form[f.key] as number) ?? ""}
                onChange={(e) =>
                  set(f.key, e.target.value === "" ? null : Number(e.target.value))
                }
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition-colors duration-200 focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
            </label>
          ))}
        </div>

        <div className="mt-4 flex flex-wrap gap-4">
          {BOOL_FIELDS.map((f) => (
            <label
              key={f.key}
              className="flex cursor-pointer items-center gap-2 text-sm text-slate-700"
            >
              <input
                type="checkbox"
                checked={Boolean(form[f.key])}
                onChange={(e) => set(f.key, e.target.checked)}
                className="h-4 w-4 cursor-pointer rounded border-slate-300 text-brand-600 focus:ring-brand-400"
              />
              {f.label}
            </label>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-1 font-semibold text-slate-900">Khoá API (ghi-only)</h2>
        <p className="mb-4 text-sm text-slate-500">
          Để trống nếu không thay đổi. Khoá đã lưu không bao giờ hiển thị lại.
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          {SECRET_FIELDS.map((f) => (
            <label key={f.key} className="block">
              <span className="mb-1 flex items-center gap-1.5 text-sm font-medium text-slate-700">
                {f.label}
                {data.secrets_set[f.key] && (
                  <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
                    đã đặt
                  </span>
                )}
              </span>
              <input
                type="password"
                value={secrets[f.key] ?? ""}
                onChange={(e) =>
                  setSecrets((s) => ({ ...s, [f.key]: e.target.value }))
                }
                placeholder="••••••••"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition-colors duration-200 focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
              />
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
