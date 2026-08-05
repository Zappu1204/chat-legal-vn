# chat-legal-vn — Frontend (Next.js 15)

Premium chat UI for the Vietnamese Legal RAG system, plus an admin dashboard.

## Stack & decisions

- **Next.js 15 (App Router) + React 19** — server/client component split.
- **Tailwind CSS** with a researched legal/professional design system
  (indigo authority + emerald "valid", Inter UI / Lora headings).
- **lucide-react** SVG icons (no emoji icons), consistent 24×24 viewBox.
- **Zustand** for client/auth state (persisted), **React Query** for server
  state (conversations, admin data) with polling for live job status.
- **react-markdown + remark-gfm** renders the streamed IRAC answer; section
  headings (Issue/Rule/Application/Conclusion) get distinct visual styles and
  inline `[citations]` are highlighted.
- **Custom SSE client** (`lib/chat-stream.ts`) — a `fetch`-based reader that
  POSTs with a Bearer token (EventSource can't), parses the typed event
  protocol, and supports cancellation via `AbortController`. Sources render as
  collapsible cards *before* the answer text streams in.

## UX details (per the design checklist)

- All interactive elements use `cursor-pointer` and `transition-colors`
  (150–200ms); no layout-shifting hover scales.
- Light-mode-first with `slate-900` body text (≥4.5:1 contrast); status badges
  pair color with a text label (color is not the only signal).
- Effective-status warnings stream as a styled blockquote.
- `prefers-reduced-motion` disables animations globally.

## Run

```bash
cd frontend
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_API_BASE if backend isn't on :8080
npm run dev                  # http://localhost:3000
```

The backend must be running (see ../backend/README.md). Log in with the
bootstrap admin account, or register a normal user.

## Structure

```
src/
├── app/
│   ├── layout.tsx, page.tsx        # root + redirect
│   ├── login/page.tsx              # login / register
│   ├── chat/page.tsx               # streaming chat
│   └── admin/page.tsx              # admin dashboard (tabbed)
├── components/
│   ├── ChatMessage, ChatComposer, SourceCards, AnswerMarkdown
│   ├── Sidebar, AuthGuard, Providers
│   └── admin/{AdminConfig, AdminUsers, AdminPipeline}
└── lib/
    ├── api.ts            # fetch wrapper w/ refresh-on-401
    ├── chat-stream.ts    # SSE parser
    ├── auth-store.ts     # zustand persisted auth
    ├── types.ts, utils.ts
```
