"use client";

/**
 * 员工端 · 今日工作台（今日速汇报）
 *
 * 页面结构：
 * 1. 今日材料缓冲区 —— 员工把截图 / 日志 / 文档直接传到这里（Redis 个人
 *    暂存，与空间无关，24h 自动清空，不进「文档管理」）。每份材料可点击
 *    跳转到查看页核对内容，误传可删除后重传。
 * 2. 今日日报生成区 —— 点击「生成今日日报」后先产出**草稿**（不入库），
 *    页面下方提供两个操作：
 *    - 「确认使用」：草稿正式归档到「日报数据库」空间的当日日期文件夹；
 *    - 「调整」：输入调整意见，AI 以上一版为基础重新生成。
 * 3. 历史日报 —— 过往日报留痕（含草稿/已入库状态）。
 *
 * 日报每天在 18:47 也会由系统定时任务自动生成草稿，手动生成只是为了
 * 「马上看到结果」。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  CheckCircle2,
  Eye,
  File,
  FileText,
  Image as ImageIcon,
  Inbox,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";
import Link from "next/link";

import {
  dailyMaterialsApi,
  type DailyMaterial,
} from "@/lib/daily-materials-api";
import { reportsApi, type DailyReport } from "@/lib/reports-api";
import { ReportContent } from "@/components/report-content";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useToast } from "@/components/ui/toast";

const STATUS_LABEL: Record<DailyReport["status"], string> = {
  generating: "生成中",
  completed: "已完成",
  failed: "生成失败",
};

const KIND_ICON: Record<DailyMaterial["kind"], typeof FileText> = {
  text: FileText,
  image: ImageIcon,
  other: File,
};

const KIND_LABEL: Record<DailyMaterial["kind"], string> = {
  text: "文本",
  image: "截图",
  other: "文件",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export default function WorkbenchPage() {
  const [materials, setMaterials] = useState<DailyMaterial[]>([]);
  const [todayReport, setTodayReport] = useState<DailyReport | null>(null);
  const [history, setHistory] = useState<DailyReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [materialsLoading, setMaterialsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [adjustMode, setAdjustMode] = useState(false);
  const [adjustPrompt, setAdjustPrompt] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { addToast } = useToast();

  // ─── 缓冲区材料（个人暂存，与空间无关） ─────────────────────────

  const loadMaterials = useCallback(async () => {
    setMaterialsLoading(true);
    try {
      setMaterials(await dailyMaterialsApi.list());
    } catch {
      addToast({ type: "error", message: "加载缓冲区材料失败" });
    } finally {
      setMaterialsLoading(false);
    }
  }, [addToast]);

  const handleFilesSelected = async (
    e: React.ChangeEvent<HTMLInputElement>
  ) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // 允许重复选择同一文件
    if (!files.length) return;

    setUploading(true);
    let ok = 0;
    for (const file of files) {
      try {
        await dailyMaterialsApi.upload(file);
        ok += 1;
      } catch (err) {
        addToast({
          type: "error",
          message: `「${file.name}」上传失败`,
          description: err instanceof Error ? err.message : undefined,
        });
      }
    }
    if (ok > 0) {
      addToast({
        type: "success",
        message: `已上传 ${ok} 份材料到缓冲区`,
        description: "材料仅暂存用于今日日报，次日自动清空，不进文档管理",
      });
    }
    setUploading(false);
    loadMaterials();
  };

  const handleDeleteMaterial = async (m: DailyMaterial) => {
    setDeletingId(m.id);
    try {
      await dailyMaterialsApi.remove(m.id);
      addToast({ type: "success", message: `已删除「${m.filename}」` });
      loadMaterials();
    } catch {
      addToast({ type: "error", message: "删除失败，请重试" });
    } finally {
      setDeletingId(null);
    }
  };

  // ─── 日报（草稿 → 确认入库） ────────────────────────────────────

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const { reports } = await reportsApi.listMine(undefined, 0, 30);
      const today = new Date().toISOString().slice(0, 10);
      setTodayReport(reports.find((r) => r.report_date === today) ?? null);
      setHistory(reports.filter((r) => r.report_date !== today));
    } catch {
      addToast({ type: "error", message: "加载日报失败" });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    loadReports();
    loadMaterials();
  }, [loadReports, loadMaterials]);

  // 有「识别中」的材料时自动轮询，直到异步识别全部结束
  useEffect(() => {
    const hasAnalyzing = materials.some(
      (m) =>
        m.kind !== "text" &&
        (m.desc_status === "pending" || m.desc_status === "processing")
    );
    if (!hasAnalyzing) return;
    const timer = setInterval(() => {
      dailyMaterialsApi
        .list()
        .then((list) =>
          // 数据没变化时保持原引用，避免整页重渲染引起输入框抖动
          setMaterials((prev) =>
            JSON.stringify(prev) === JSON.stringify(list) ? prev : list
          )
        )
        .catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [materials]);

  const handleGenerate = async (opts?: {
    force?: boolean;
    adjustPrompt?: string;
  }) => {
    setGenerating(true);
    try {
      const report = await reportsApi.generate({
        force: opts?.force ?? false,
        adjustPrompt: opts?.adjustPrompt,
      });
      setTodayReport(report);
      setAdjustMode(false);
      setAdjustPrompt("");
      addToast({
        type: "success",
        message: opts?.adjustPrompt ? "已按意见重新生成" : "日报草稿生成成功",
        description: "确认无误后点击下方「确认使用」正式入库",
      });
      loadReports();
    } catch (err) {
      addToast({
        type: "error",
        message: "日报生成失败",
        description:
          err instanceof Error ? err.message : "请先上传今日工作材料",
      });
    } finally {
      setGenerating(false);
    }
  };

  const handleConfirm = async () => {
    if (!todayReport) return;
    setConfirming(true);
    try {
      const report = await reportsApi.confirm(todayReport.id);
      setTodayReport(report);
      addToast({
        type: "success",
        message: "日报已确认入库",
        description: "已归档到「日报数据库」当日文件夹，主管可以随时检索到",
      });
      loadReports();
    } catch (err) {
      addToast({
        type: "error",
        message: "确认入库失败",
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setConfirming(false);
    }
  };

  // ─── 渲染 ────────────────────────────────────────────────────────

  const isDraft = todayReport?.status === "completed" && !todayReport.confirmed;
  const isConfirmed = todayReport?.status === "completed" && todayReport.confirmed;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">今日工作台</h1>
        <p className="text-sm text-muted-foreground">
          把今天的工作材料传进缓冲区，AI 一键为你生成当日工作日报
        </p>
      </div>

      {/* 今日材料缓冲区 */}
      <Card className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Inbox className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">今日材料缓冲区</h2>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {materials.length} 份
            </span>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFilesSelected}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="mr-1 h-4 w-4" />
              )}
              上传材料
            </Button>
          </div>
        </div>

        <Separator className="my-4" />

        <p className="mb-3 text-xs text-muted-foreground">
          缓冲区是你的个人暂存区，与空间无关：材料仅用于生成今日日报，
          次日自动清空，不会进入「文档管理」的长期知识库。传错了可以随时删除重传。
        </p>

        {materialsLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : materials.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            缓冲区是空的。点击「上传材料」把今天的截图、日志或文档放进来。
          </p>
        ) : (
          <ul className="divide-y">
            {materials.map((m) => {
              const Icon = KIND_ICON[m.kind];
              return (
                <li key={m.id} className="flex items-center gap-3 py-2.5">
                  <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{m.filename}</p>
                    <p className="text-xs text-muted-foreground">
                      {KIND_LABEL[m.kind]} · {formatSize(m.size)} ·{" "}
                      {formatTime(m.uploaded_at)} 上传
                    </p>
                  </div>
                  {/* 异步识别状态徽章（text 类无需识别，不显示） */}
                  {m.kind !== "text" &&
                    (m.desc_status === "pending" ||
                    m.desc_status === "processing" ? (
                      <span className="flex shrink-0 items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        识别中
                      </span>
                    ) : m.desc_status === "done" ? (
                      <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">
                        已识别
                      </span>
                    ) : (
                      <span className="shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700">
                        识别失败
                      </span>
                    ))}
                  <Button variant="ghost" size="sm" asChild>
                    <Link href={`/workbench/materials/${m.id}`}>
                      <Eye className="mr-1 h-4 w-4" />
                      查看
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    disabled={deletingId === m.id}
                    onClick={() => handleDeleteMaterial(m)}
                  >
                    {deletingId === m.id ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="mr-1 h-4 w-4" />
                    )}
                    删除
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {/* 今日日报生成区 */}
      <Card className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">
              今日日报 · {new Date().toLocaleDateString("zh-CN")}
            </h2>
            {todayReport && (
              <span
                className={
                  todayReport.status === "completed"
                    ? todayReport.confirmed
                      ? "rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700"
                      : "rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700"
                    : todayReport.status === "failed"
                      ? "rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700"
                      : "rounded-full bg-yellow-100 px-2 py-0.5 text-xs text-yellow-700"
                }
              >
                {todayReport.status === "completed"
                  ? todayReport.confirmed
                    ? "已入库"
                    : "草稿待确认"
                  : STATUS_LABEL[todayReport.status]}
              </span>
            )}
          </div>
          {!todayReport && (
            <Button
              size="sm"
              disabled={generating || materials.length === 0}
              onClick={() => handleGenerate()}
            >
              {generating ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-1 h-4 w-4" />
              )}
              生成今日日报
            </Button>
          )}
        </div>

        <Separator className="my-4" />

        {generating ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            AI 正在聚合缓冲区 {materials.length} 份材料生成日报，截图较多时
            需要 1-2 分钟，请稍候…
          </div>
        ) : loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : todayReport?.status === "completed" ? (
          <>
            <ReportContent content={todayReport.content} />

            <Separator className="my-4" />

            {isDraft && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    disabled={confirming || generating}
                    onClick={handleConfirm}
                  >
                    {confirming ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                      <CheckCircle2 className="mr-1 h-4 w-4" />
                    )}
                    确认使用
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={confirming || generating}
                    onClick={() => setAdjustMode((v) => !v)}
                  >
                    <Wand2 className="mr-1 h-4 w-4" />
                    调整
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    草稿暂不入库，确认后才会归档到「日报数据库」
                  </span>
                </div>

                {adjustMode && (
                  <div className="space-y-2 rounded-md border bg-muted/40 p-3">
                    <textarea
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-primary"
                      rows={3}
                      placeholder="输入调整意见，例如：「今日完成部分再详细一点」「把风险部分精简」「补充明日计划」…"
                      value={adjustPrompt}
                      onChange={(e) => setAdjustPrompt(e.target.value)}
                    />
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={generating || !adjustPrompt.trim()}
                      onClick={() =>
                        handleGenerate({
                          force: true,
                          adjustPrompt: adjustPrompt.trim(),
                        })
                      }
                    >
                      {generating ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                      ) : (
                        <RefreshCw className="mr-1 h-4 w-4" />
                      )}
                      按意见重新生成
                    </Button>
                  </div>
                )}
              </div>
            )}

            {isConfirmed && (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                已确认入库
                {todayReport.confirmed_at &&
                  ` · ${new Date(todayReport.confirmed_at).toLocaleString("zh-CN")}`}
                ，已归档到「日报数据库」当日文件夹
              </p>
            )}
          </>
        ) : todayReport?.status === "failed" ? (
          <div className="space-y-3">
            <p className="text-sm text-destructive">
              生成失败：{todayReport.error_detail || "未知错误"}
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={generating}
              onClick={() => handleGenerate({ force: true })}
            >
              <RefreshCw className="mr-1 h-4 w-4" />
              重新生成
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            今天还没有日报。先把材料传到上方缓冲区，然后点击
            「生成今日日报」。每天 18:47 系统也会自动生成草稿。
          </p>
        )}
      </Card>

      {/* 历史日报 */}
      <Card className="p-6">
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">历史日报</h2>
        </div>
        <Separator className="my-4" />
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无历史日报</p>
        ) : (
          <ul className="divide-y">
            {history.map((r) => (
              <li
                key={r.id}
                className="flex items-center justify-between py-2 text-sm"
              >
                <span className="font-medium">{r.report_date}</span>
                <span
                  className={
                    r.status === "completed"
                      ? r.confirmed
                        ? "text-green-600"
                        : "text-blue-600"
                      : r.status === "failed"
                        ? "text-destructive"
                        : "text-muted-foreground"
                  }
                >
                  {r.status === "completed"
                    ? r.confirmed
                      ? "已入库"
                      : "草稿"
                    : STATUS_LABEL[r.status]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
