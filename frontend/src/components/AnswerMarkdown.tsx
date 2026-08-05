"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders the streamed Markdown IRAC answer. The `.irac` scope (see
 * globals.css) gives Issue / Rule / Application / Conclusion headings a left
 * accent rule. Bracketed citations like [Nghị định 168/2024/NĐ-CP - Điều 7]
 * are highlighted inline via a custom text transform.
 */
export function AnswerMarkdown({ content }: { content: string }) {
  return (
    <div className="irac">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Highlight inline [citations] without breaking markdown flow.
          p: ({ children }) => <p>{highlightCitations(children)}</p>,
          li: ({ children }) => <li>{highlightCitations(children)}</li>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function highlightCitations(children: React.ReactNode): React.ReactNode {
  if (typeof children === "string") return splitAndMark(children);
  if (Array.isArray(children)) {
    return children.map((c, i) =>
      typeof c === "string" ? (
        <span key={i}>{splitAndMark(c)}</span>
      ) : (
        c
      ),
    );
  }
  return children;
}

const CITATION_RE = /(\[[^\]]+\])/g;

function splitAndMark(text: string): React.ReactNode {
  const parts = text.split(CITATION_RE);
  return parts.map((part, i) =>
    CITATION_RE.test(part) ? (
      <mark
        key={i}
        className="rounded bg-brand-50 px-1 font-medium text-brand-700"
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}
