/**
 * 今日材料缓冲区 API 封装（员工端 · 今日工作台）。
 *
 * 后端路由（材料只进 Redis 缓冲区，不进长期知识库）：
 * - POST   /api/daily-materials/upload       上传今日材料（multipart）
 * - GET    /api/daily-materials              当日缓冲区材料列表
 * - GET    /api/daily-materials/{id}         材料详情（文本类附正文）
 * - GET    /api/daily-materials/{id}/raw     材料原始字节（图片预览/下载）
 * - DELETE /api/daily-materials/{id}         删除误传材料
 */

import { apiClient } from "@/lib/api-client";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface DailyMaterial {
  id: string;
  space_id: string;
  user_id: string;
  date: string;
  filename: string;
  content_type: string;
  size: number;
  kind: "text" | "image" | "other";
  uploaded_at: string;
  /** 异步识别状态：text 类上传即 done */
  desc_status: "pending" | "processing" | "done" | "failed";
  /** 图片的 AI 识别描述（done 后有值） */
  ai_description: string | null;
  /** 文档类提取的文本（done 后有值） */
  extracted_text: string | null;
}

export interface DailyMaterialDetail extends DailyMaterial {
  text_content: string | null;
}

/** 带 JWT 的原生 fetch（multipart / blob 场景，apiClient 只支持 JSON） */
async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = apiClient.getAccessToken();
  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string>) || {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (resp.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  return resp;
}

async function extractError(resp: Response): Promise<string> {
  try {
    const data = await resp.json();
    const detail = (data as { detail?: unknown })?.detail;
    if (typeof detail === "string") return detail;
    const msg = (data as { message?: unknown })?.message;
    if (typeof msg === "string") return msg;
  } catch {
    /* ignore */
  }
  return `请求失败（${resp.status}）`;
}

/** 通用：带 JWT 拉取任意 API 路径的 blob（日报内嵌图片等场景复用） */
export async function fetchApiBlob(path: string): Promise<Blob> {
  const resp = await authFetch(path);
  if (!resp.ok) throw new Error(await extractError(resp));
  return resp.blob();
}

export const dailyMaterialsApi = {
  /** 当日缓冲区材料列表（个人暂存，与空间无关） */
  list: async (reportDate?: string): Promise<DailyMaterial[]> => {
    const params = new URLSearchParams();
    if (reportDate) params.set("report_date", reportDate);
    const qs = params.toString();
    const data = await apiClient.get<{
      materials: DailyMaterial[];
      total: number;
    }>(`/api/daily-materials${qs ? `?${qs}` : ""}`);
    return data.materials;
  },

  /** 上传一份材料到我的缓冲区（无需空间） */
  upload: async (file: File): Promise<DailyMaterial> => {
    const form = new FormData();
    form.append("file", file);
    const resp = await authFetch("/api/daily-materials/upload", {
      method: "POST",
      body: form,
    });
    if (!resp.ok) throw new Error(await extractError(resp));
    return resp.json();
  },

  /** 材料详情（查看页） */
  get: (materialId: string) =>
    apiClient.get<DailyMaterialDetail>(`/api/daily-materials/${materialId}`),

  /** 取材料原始字节（图片预览 / 文件下载用） */
  fetchRawBlob: async (materialId: string): Promise<Blob> => {
    const resp = await authFetch(`/api/daily-materials/${materialId}/raw`);
    if (!resp.ok) throw new Error(await extractError(resp));
    return resp.blob();
  },

  /** 删除误传材料 */
  remove: (materialId: string) =>
    apiClient.delete<void>(`/api/daily-materials/${materialId}`),
};
