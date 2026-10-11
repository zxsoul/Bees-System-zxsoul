"use client";

/**
 * 日报内容渲染组件（图文并茂）。
 *
 * 日报正文是 Markdown，其中内嵌图片指向受 JWT 保护的接口
 * （/api/reports/{id}/assets/{materialId}）。普通 <img> 标签带不了
 * Authorization 头，因此自定义 img 渲染器：带 token 拉取 blob →
 * 本地 ObjectURL 显示。
 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { fetchApiBlob } from "@/lib/daily-materials-api";

function AuthImage({ src, alt }: { src?: string; alt?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    (async () => {
      if (!src) {
        setFailed(true);
        return;
      }
      try {
        const blob = await fetchApiBlob(src);
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setUrl(objectUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  if (failed) {
    return (
      <span className="text-xs text-muted-foreground">（图片加载失败{alt ? `：${alt}` : ""}）</span>
    );
  }
  if (!url) {
    return (
      <span className="block h-24 animate-pulse rounded-md bg-muted" aria-label="图片加载中" />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt={alt || "日报插图"}
      className="my-2 max-h-[480px] w-auto max-w-full rounded-md border"
    />
  );
}

export function ReportContent({ content }: { content: string }) {
  return (
    <article className="prose prose-sm max-w-none dark:prose-invert">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: (props) => (
            <AuthImage
              src={typeof props.src === "string" ? props.src : undefined}
              alt={props.alt}
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
