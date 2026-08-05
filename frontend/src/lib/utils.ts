import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  effective: {
    label: "Còn hiệu lực",
    cls: "bg-emerald-50 text-emerald-700 border-emerald-200",
  },
  expired: {
    label: "Hết hiệu lực",
    cls: "bg-rose-50 text-rose-700 border-rose-200",
  },
  partially_expired: {
    label: "Hết hiệu lực một phần",
    cls: "bg-amber-50 text-amber-700 border-amber-200",
  },
  not_yet_effective: {
    label: "Chưa có hiệu lực",
    cls: "bg-sky-50 text-sky-700 border-sky-200",
  },
  unknown: {
    label: "Chưa rõ hiệu lực",
    cls: "bg-slate-100 text-slate-600 border-slate-200",
  },
};

export function statusBadge(status: string | null | undefined) {
  return STATUS_LABELS[status ?? "unknown"] ?? STATUS_LABELS.unknown;
}
