"use client";

/**
 * 缓冲区材料查看页（今日工作台 → 点击某份材料的「查看」跳转到这里）
 *
 * - 文本类材料：直接展示正文内容；
 * - 截图类材料：取原始字节渲染图片预览；
 * - 其它类型：提供下载。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Download,
  File as FileIcon,
  Loader2,
} from "lucide-react";

import {
  dailyMaterialsApi,
  type DailyMaterialDetail,
} from "@/lib/daily-materials-api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function MaterialViewerPage() {
  const params = useParams<{ id: string }>();
  const materialId = params.id;

  const [detail, setDetail] = useState<DailyMaterialDetail | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    (async () => {
      try {
        const data = await dailyMaterialsApi.get(materialId);
        setDetail(data);
        if (data.kind === "image") {
          const blob = await dailyMaterialsApi.fetchRawBlob(materialId);
          objectUrl = URL.createObjectURL(blob);
          setBlobUrl(objectUrl);
        }
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "材料不存在或已过期被清除"
        );
      } finally {
        setLoading(false);
      }
    })();
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [materialId]);

  const handleDownload = async () => {
    if (!detail) return;
    try {
      const blob = await dailyMaterialsApi.fetchRawBlob(materialId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = detail.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("下载失败，请重试");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/workbench">
            <ArrowLeft className="mr-1 h-4 w-4" />
            返回工作台
          </Link>
        </Button>
      </div>

      <Card className="p-6">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载材料…
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : detail ? (
          <>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileIcon className="h-5 w-5 text-primary" />
                <h1 className="text-lg font-semibold">{detail.filename}</h1>
              </div>
              <Button variant="outline" size="sm" onClick={handleDownload}>
                <Download className="mr-1 h-4 w-4" />
                下载
              </Button>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {formatSize(detail.size)} · 上传于{" "}
              {new Date(detail.uploaded_at).toLocaleString("zh-CN")} ·
              缓冲区临时材料，到期自动清除
            </p>
            <Separator className="my-4" />

            {detail.kind === "image" ? (
              blobUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={blobUrl}
                  alt={detail.filename}
                  className="max-h-[70vh] w-auto max-w-full rounded-md border"
                />
              ) : (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> 加载图片…
                </div>
              )
            ) : detail.kind === "text" ? (
              <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-muted p-4 text-sm">
                {detail.text_content || "（内容为空）"}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">
                该文件类型暂不支持在线预览，可点击右上角「下载」查看。
              </p>
            )}
          </>
        ) : null}
      </Card>
    </div>
  );
}
