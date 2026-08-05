"use client";

import { useAuthStore } from "./auth-store";
import type { CitationDTO, SourceCard } from "./types";

export interface ChatStreamCallbacks {
  onMeta?: (conversationId: string) => void;
  onSources?: (sources: SourceCard[]) => void;
  onTextDelta?: (delta: string) => void;
  onCitations?: (citations: CitationDTO[], warnings: string[]) => void;
  onDone?: (info: { timings: Record<string, number>; revision: number }) => void;
  onError?: (message: string) => void;
}

/**
 * Stream a chat answer over SSE.
 *
 * `EventSource` cannot POST or set Authorization headers, so we use `fetch`
 * with a readable-stream body and parse the SSE framing manually. Events are
 * delimited by a blank line; each has an `event:` name and a `data:` JSON
 * payload. An `AbortController` lets the UI cancel an in-flight generation.
 */
export async function streamChat(
  query: string,
  conversationId: string | null,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const { accessToken } = useAuthStore.getState();

  const res = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({ query, conversation_id: conversationId }),
    signal,
  });

  if (!res.ok || !res.body) {
    callbacks.onError?.("Không thể kết nối tới máy chủ.");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (event: string, data: string) => {
    let payload: unknown = {};
    try {
      payload = JSON.parse(data);
    } catch {
      return;
    }
    const p = payload as Record<string, unknown>;
    switch (event) {
      case "meta":
        callbacks.onMeta?.(String(p.conversation_id ?? ""));
        break;
      case "sources":
        callbacks.onSources?.((p.sources as SourceCard[]) ?? []);
        break;
      case "text-delta":
        callbacks.onTextDelta?.(String(p.delta ?? ""));
        break;
      case "citations":
        callbacks.onCitations?.(
          (p.citations as CitationDTO[]) ?? [],
          (p.warnings as string[]) ?? [],
        );
        break;
      case "done":
        callbacks.onDone?.({
          timings: (p.timings as Record<string, number>) ?? {},
          revision: Number(p.revision ?? 0),
        });
        break;
      case "error":
        callbacks.onError?.(String(p.message ?? "Đã xảy ra lỗi."));
        break;
    }
  };

  // Parse a complete SSE block ("event: x\ndata: y").
  const parseBlock = (block: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) dispatch(event, dataLines.join("\n"));
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      // Events are separated by a blank line (\n\n).
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (block.trim()) parseBlock(block);
      }
    }
    if (buffer.trim()) parseBlock(buffer);
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      callbacks.onError?.("Mất kết nối khi nhận câu trả lời.");
    }
  }
}
