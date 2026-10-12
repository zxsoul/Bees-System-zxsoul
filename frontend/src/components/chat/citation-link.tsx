"use client";

import { FileText } from "lucide-react";
import { type Citation, useChatStore } from "@/stores/chat-store";

interface CitationLinkProps {
  citation: Citation;
  index: number;
}

export function CitationLink({ citation, index }: CitationLinkProps) {
  const currentSessionId = useChatStore((s) => s.currentSessionId);

  const handleClick = () => {
    // Navigate to document with highlight parameter
    const params = new URLSearchParams({
      highlight: String(citation.chunk_index),
    });
    // 记录来源页面（含当前会话 ID），文档详情页的「返回」按钮据此
    // 回到原对话而不是固定落到文档列表。
    params.set(
      "from",
      currentSessionId ? `/chat?session=${currentSessionId}` : "/chat"
    );
    window.location.href = `/documents/${citation.document_id}?${params.toString()}`;
  };

  return (
    <button
      onClick={handleClick}
      className="inline-flex items-center gap-1 rounded-md border bg-muted/50 px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      title={citation.title_chain || citation.document_title}
    >
      <FileText className="h-3 w-3" />
      <span className="max-w-[200px] truncate">
        [{index + 1}] {citation.document_title}
      </span>
      {citation.title_chain && (
        <span className="hidden sm:inline text-muted-foreground/70">
          · {citation.title_chain}
        </span>
      )}
    </button>
  );
}
