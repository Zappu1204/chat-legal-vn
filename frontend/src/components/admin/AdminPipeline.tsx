"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Loader2, Play, CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import type { PipelineJob } from "@/lib/types";

export function AdminPipeline() {
  const qc = useQueryClient();
  const [limit, setLimit] = useState(0);
  const [triggering, setTriggering] = useState(false);

  const { data: jobs = [] } = useQuery<PipelineJob[]>({
    queryKey: ["pipeline-jobs"],
    queryFn: () => api.listJobs() as Promise<PipelineJob[]>,
    // Poll while any job is running so the progress reflects live status.
    refetchInterval: (q) => {
      const data = q.state.data as PipelineJob[] | undefined;
      return data?.some((j) => j.status === "running") ? 2000 : false;
    },
  });

  async function trigger() {
    setTriggering(true);
    try {
      await api.triggerIngest(limit);
      qc.invalidateQueries({ queryKey: ["pipeline-jobs"] });
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setTriggering(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <Database className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="flex-1">
            <h2 className="font-semibold text-slate-900">Lập chỉ mục tài liệu</h2>
            <p className="text-sm text-slate-500">
              Chạy pipeline ingest khi có dữ liệu parquet mới. Chỉ một tác vụ
              chạy tại một thời điểm.
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">
              Giới hạn số tài liệu (0 = toàn bộ)
            </span>
            <input
              type="number"
              min={0}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="w-48 rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none transition-colors duration-200 focus:border-brand-500 focus:ring-2 focus:ring-brand-200"
            />
          </label>
          <button
            onClick={trigger}
            disabled={triggering}
            className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition-colors duration-200 hover:bg-brand-700 disabled:opacity-60"
          >
            {triggering ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="h-4 w-4" aria-hidden="true" />
            )}
            Chạy ingest
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-3 font-semibold text-slate-900">Lịch sử tác vụ</h3>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-400">Chưa có tác vụ nào.</p>
        ) : (
          <ul className="space-y-2">
            {jobs.map((j) => (
              <li
                key={j.id}
                className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2.5"
              >
                <JobStatusIcon status={j.status} />
                <div className="flex-1">
                  <p className="text-sm font-medium text-slate-800">
                    {j.kind}
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      {new Date(j.started_at).toLocaleString("vi-VN")}
                    </span>
                  </p>
                  {j.error && (
                    <p className="text-xs text-rose-600">{j.error}</p>
                  )}
                  {j.result && (
                    <p className="text-xs text-slate-500">
                      {JSON.stringify(j.result)}
                    </p>
                  )}
                </div>
                <span className="text-xs capitalize text-slate-500">{j.status}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function JobStatusIcon({ status }: { status: PipelineJob["status"] }) {
  if (status === "running")
    return <Loader2 className="h-5 w-5 animate-spin text-brand-500" aria-hidden="true" />;
  if (status === "succeeded")
    return <CheckCircle2 className="h-5 w-5 text-emerald-500" aria-hidden="true" />;
  return <XCircle className="h-5 w-5 text-rose-500" aria-hidden="true" />;
}
