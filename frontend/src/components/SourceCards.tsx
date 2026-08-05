"use client";

import { ChevronDown, FileText, Landmark } from "lucide-react";
import { useState } from "react";
import type { SourceCard } from "@/lib/types";
import { cn, statusBadge } from "@/lib/utils";

function SourceCardItem({ card }: { card: SourceCard }) {
  const [open, setOpen] = useState(false);
  const badge = statusBadge(card.effective_status);
  const title =
    [card.loai_van_ban, card.so_ky_hieu].filter(Boolean).join(" ") ||
    `Tài liệu #${card.document_id}`;

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white transition-colors duration-200 hover:border-brand-300">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-start gap-3 px-3 py-2.5 text-left"
      >
        <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-600">
          <FileText className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-900">
              {title}
            </span>
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                badge.cls,
              )}
            >
              {badge.label}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-slate-500">
            {card.hierarchical_path}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform duration-200",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="animate-fade-in border-t border-slate-100 px-3 py-3 text-sm">
          {card.co_quan_ban_hanh && (
            <p className="mb-2 flex items-center gap-1.5 text-xs text-slate-500">
              <Landmark className="h-3.5 w-3.5" aria-hidden="true" />
              {card.co_quan_ban_hanh}
              {card.ngay_ban_hanh ? ` · ${card.ngay_ban_hanh}` : ""}
            </p>
          )}
          <p className="leading-relaxed text-slate-700">{card.snippet}</p>
          <p className="mt-2 text-[11px] text-slate-400">
            Độ liên quan: {(card.score * 100).toFixed(1)}% · nguồn: {card.source}
          </p>
        </div>
      )}
    </div>
  );
}

export function SourceCards({ sources }: { sources: SourceCard[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Nguồn tham chiếu ({sources.length})
      </p>
      <div className="grid gap-2">
        {sources.map((s) => (
          <SourceCardItem key={s.chunk_id} card={s} />
        ))}
      </div>
    </div>
  );
}
