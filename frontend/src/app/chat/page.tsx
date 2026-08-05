"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { ChatComposer } from "@/components/ChatComposer";
import { ChatMessage, type ChatMessageData } from "@/components/ChatMessage";
import { Sidebar } from "@/components/Sidebar";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/chat-stream";
import type { ConversationDetail, MessagePublic } from "@/lib/types";

const SUGGESTIONS = [
  "Vượt đèn đỏ và không đội mũ bảo hiểm bị phạt thế nào?",
  "Nồng độ cồn vượt mức cho phép bị xử phạt ra sao?",
  "Điểm h khoản 2 Điều 7 Nghị định 168/2024/NĐ-CP quy định gì?",
];

function ChatView() {
  const qc = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const loadConversation = useCallback(async (id: string) => {
    setConversationId(id);
    const detail = (await api.getConversation(id)) as ConversationDetail;
    setMessages(
      detail.messages.map((m: MessagePublic) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        sources: m.meta?.sources,
      })),
    );
  }, []);

  function newConversation() {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setStreaming(false);
  }

  async function send(text: string) {
    const userMsg: ChatMessageData = {
      id: `u-${Date.now()}`,
      role: "user",
      content: text,
    };
    const assistantId = `a-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const patch = (fn: (m: ChatMessageData) => ChatMessageData) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? fn(m) : m)),
      );

    await streamChat(
      text,
      conversationId,
      {
        onMeta: (cid) => {
          if (!conversationId && cid) setConversationId(cid);
        },
        onSources: (sources) => patch((m) => ({ ...m, sources })),
        onTextDelta: (delta) =>
          patch((m) => ({ ...m, content: m.content + delta })),
        onDone: () => {
          patch((m) => ({ ...m, streaming: false }));
          setStreaming(false);
          qc.invalidateQueries({ queryKey: ["conversations"] });
        },
        onError: (msg) => {
          patch((m) => ({
            ...m,
            streaming: false,
            content:
              m.content || `_${msg}_`,
          }));
          setStreaming(false);
        },
      },
      controller.signal,
    );
  }

  function stop() {
    abortRef.current?.abort();
    setStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <Sidebar
        activeId={conversationId}
        onSelect={loadConversation}
        onNew={newConversation}
      />
      <main className="flex flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-4 text-center">
              <span className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 text-white">
                <Scale className="h-7 w-7" aria-hidden="true" />
              </span>
              <h1 className="font-serif text-2xl font-semibold text-slate-900">
                Hỏi đáp pháp luật giao thông
              </h1>
              <p className="mt-2 text-slate-600">
                Mọi câu trả lời đều kèm trích dẫn điều khoản cụ thể từ văn bản
                pháp luật.
              </p>
              <div className="mt-6 grid w-full gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="cursor-pointer rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 transition-colors duration-200 hover:border-brand-300 hover:bg-brand-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {messages.map((m) => (
                <ChatMessage key={m.id} message={m} />
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>
        <ChatComposer onSend={send} onStop={stop} streaming={streaming} />
      </main>
    </div>
  );
}

export default function ChatPage() {
  return (
    <AuthGuard>
      <ChatView />
    </AuthGuard>
  );
}
