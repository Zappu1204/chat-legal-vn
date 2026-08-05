"use client";

import { Send, Square } from "lucide-react";
import { useRef, useState } from "react";

export function ChatComposer({
  onSend,
  onStop,
  streaming,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const t = text.trim();
    if (!t || streaming) return;
    onSend(t);
    setText("");
    if (ref.current) ref.current.style.height = "auto";
  }

  return (
    <div className="border-t border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto max-w-3xl px-4 py-3">
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm transition-colors duration-200 focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-100">
          <textarea
            ref={ref}
            rows={1}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Hỏi về luật giao thông đường bộ… (Enter để gửi, Shift+Enter xuống dòng)"
            className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1.5 text-slate-900 outline-none placeholder:text-slate-400"
            aria-label="Nội dung câu hỏi"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl bg-slate-200 text-slate-700 transition-colors duration-200 hover:bg-slate-300"
              aria-label="Dừng tạo câu trả lời"
            >
              <Square className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!text.trim()}
              className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl bg-brand-600 text-white transition-colors duration-200 hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Gửi câu hỏi"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[11px] text-slate-400">
          Câu trả lời dựa trên văn bản pháp luật đã lập chỉ mục, có thể chưa đầy đủ.
        </p>
      </div>
    </div>
  );
}
