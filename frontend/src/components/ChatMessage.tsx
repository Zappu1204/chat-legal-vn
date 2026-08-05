"use client";

import { Scale, User as UserIcon, Loader2 } from "lucide-react";
import type { SourceCard } from "@/lib/types";
import { AnswerMarkdown } from "./AnswerMarkdown";
import { SourceCards } from "./SourceCards";

export interface ChatMessageData {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceCard[];
  streaming?: boolean;
}

export function ChatMessage({ message }: { message: ChatMessageData }) {
  const isUser = message.role === "user";

  return (
    <div className="animate-fade-in py-4">
      <div className="mx-auto flex max-w-3xl gap-3 px-4">
        <span
          className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
            isUser ? "bg-slate-200 text-slate-700" : "bg-brand-600 text-white"
          }`}
          aria-hidden="true"
        >
          {isUser ? <UserIcon className="h-4 w-4" /> : <Scale className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-xs font-semibold text-slate-500">
            {isUser ? "Bạn" : "Trợ lý pháp luật"}
          </p>

          {isUser ? (
            <p className="whitespace-pre-wrap leading-relaxed text-slate-800">
              {message.content}
            </p>
          ) : (
            <>
              {/* Sources render first (grounding before answer). */}
              {message.sources && message.sources.length > 0 && (
                <SourceCards sources={message.sources} />
              )}
              <div className="mt-3">
                {message.content ? (
                  <AnswerMarkdown content={message.content} />
                ) : message.streaming ? (
                  <p className="flex items-center gap-2 text-sm text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Đang phân tích tài liệu và soạn câu trả lời…
                  </p>
                ) : null}
                {message.streaming && message.content && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-brand-400 align-middle" />
                )}
              </div>
              {!isUser && message.content && !message.streaming && (
                <p className="mt-4 border-t border-slate-100 pt-3 text-xs italic text-slate-400">
                  Thông tin mang tính tham khảo, không thay thế tư vấn pháp lý
                  chuyên nghiệp.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
