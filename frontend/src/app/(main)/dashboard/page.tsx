"use client";

/**
 * 概览页 · 产品导览
 *
 * 承担两个职责:
 * 1. 新用户 / 面试官的第一站: 顶部「体验动线」按真实使用顺序串起
 *    员工端 → 主管端的完整闭环, 每步可点击直达;
 * 2. 功能地图: 按角色分组介绍每个模块的实际能力, 点击卡片跳转。
 *
 * 文案描述的是各模块的真实行为 (与页面实现保持一致), 不是营销话术。
 */

import Link from "next/link";
import {
  ArrowRight,
  BellRing,
  ClipboardList,
  FileText,
  FolderTree,
  MessageSquare,
  Search,
  Settings,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { useAuthStore } from "@/stores/auth-store";

interface Feature {
  href: string;
  icon: typeof ClipboardList;
  title: string;
  tagline: string;
  points: string[];
}

interface FeatureGroup {
  label: string;
  description: string;
  features: Feature[];
}

const GROUPS: FeatureGroup[] = [
  {
    label: "员工端",
    description: "每天几分钟, 把材料丢进来, 汇报交给 AI",
    features: [
      {
        href: "/workbench",
        icon: ClipboardList,
        title: "今日工作台",
        tagline: "上传今日材料, 一键生成日报",
        points: [
          "截图 / 日志 / 文档传进个人缓冲区, 仅暂存 24 小时, 不进长期知识库",
          "AI 聚合当日材料生成日报草稿, 可输入意见反复调整, 确认后才正式入库",
          "每天 18:47 系统自动生成草稿, 历史日报全部留痕",
        ],
      },
      {
        href: "/documents",
        icon: FileText,
        title: "工作材料",
        tagline: "团队的长期知识库",
        points: [
          "文档上传后自动解析、切块、向量化, 进入 RAG 检索链路",
          "文件夹 + 标签双维度管理, 支持 PDF 等复杂版面解析",
        ],
      },
    ],
  },
  {
    label: "主管端",
    description: "不开会、不催人, 碎片时间掌握团队进展",
    features: [
      {
        href: "/alerts",
        icon: BellRing,
        title: "进度中心",
        tagline: "停滞预警 + 全员日报一览",
        points: [
          "每晚 21:23 自动分析近 7 天日报, 检出停滞 / 消失 / 风险反复的工作项",
          "按日期翻阅全员日报原文, 预警确认后进入跟进状态",
        ],
      },
      {
        href: "/chat",
        icon: MessageSquare,
        title: "进度问答",
        tagline: "用自然语言追问任何细节",
        points: [
          "直接问「XX 项目这周进展如何」, AI 基于已入库的日报和文档回答",
          "答案附引用来源, 点击可回溯到原文片段, 数据可查可验",
        ],
      },
    ],
  },
  {
    label: "基础能力",
    description: "支撑双端运转的底层模块",
    features: [
      {
        href: "/search",
        icon: Search,
        title: "搜索",
        tagline: "全文 + 语义混合检索",
        points: [
          "OpenSearch 混合检索, 命中片段高亮显示",
          "按标题层级和页码定位到原文位置",
        ],
      },
      {
        href: "/spaces",
        icon: FolderTree,
        title: "团队空间",
        tagline: "按团队划分知识边界",
        points: [
          "日报与文档按空间归属, 权限隔离",
          "空间创建者即主管, 自动获得该空间的预警与日报查看权",
        ],
      },
    ],
  },
];

const ADMIN_FEATURE: Feature = {
  href: "/admin",
  icon: Settings,
  title: "系统管理",
  tagline: "用户、空间与模型配置",
  points: [
    "用户管理与「全员日报查看」权限授予",
    "空间成员管理、模型与运行状态监控",
  ],
};

/** 面试官 / 新用户体验动线: 按真实使用顺序串起闭环 */
const FLOW_STEPS = [
  {
    href: "/workbench",
    title: "传材料, 生成日报",
    detail: "在工作台上传几份截图或日志, 点「生成今日日报」",
  },
  {
    href: "/documents",
    title: "确认入库, 沉淀知识",
    detail: "日报确认后归档, 文档也可上传进长期知识库",
  },
  {
    href: "/chat",
    title: "问答追问细节",
    detail: "问一句「今天完成了什么」, 答案带引用来源",
  },
  {
    href: "/alerts",
    title: "主管视角看预警",
    detail: "系统自动检出停滞工作项, 无需口头汇报",
  },
];

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      {/* 页头 */}
      <div>
        <h1 className="text-2xl font-bold">概览</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          欢迎使用 Wikforge 智能工作汇报与进度洞察系统 ——
          员工免写汇报, 主管免开汇报会, 进度每天自动沉淀、可查可追问。
        </p>
      </div>

      {/* 体验动线 */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">
          第一次使用? 按这个顺序体验完整闭环
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {FLOW_STEPS.map((step, i) => (
            <Link key={step.href + i} href={step.href} className="group">
              <Card className="h-full p-4 transition-colors group-hover:border-primary/50">
                <div className="mb-2 flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
                    {i + 1}
                  </span>
                  <span className="text-sm font-semibold">{step.title}</span>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {step.detail}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      {/* 功能地图 */}
      {GROUPS.map((group) => (
        <section key={group.label}>
          <div className="mb-3">
            <h2 className="text-sm font-semibold">
              {group.label}
              <span className="ml-2 font-normal text-muted-foreground">
                {group.description}
              </span>
            </h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {group.features.map((f) => (
              <Link key={f.href} href={f.href} className="group">
                <Card className="flex h-full flex-col p-5 transition-colors group-hover:border-primary/50">
                  <div className="mb-1 flex items-center gap-2">
                    <f.icon className="h-5 w-5 text-primary" />
                    <span className="font-semibold">{f.title}</span>
                    <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </div>
                  <p className="mb-3 text-sm text-muted-foreground">
                    {f.tagline}
                  </p>
                  <ul className="space-y-1.5 text-xs leading-relaxed text-muted-foreground">
                    {f.points.map((p, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                        {p}
                      </li>
                    ))}
                  </ul>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      ))}

      {/* 系统管理: 仅管理员可见 (与 sidebar 逻辑一致) */}
      {user?.is_admin && (
        <section>
          <div className="mb-3">
            <h2 className="text-sm font-semibold">
              管理
              <span className="ml-2 font-normal text-muted-foreground">
                仅管理员可见
              </span>
            </h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Link href={ADMIN_FEATURE.href} className="group">
              <Card className="flex h-full flex-col p-5 transition-colors group-hover:border-primary/50">
                <div className="mb-1 flex items-center gap-2">
                  <ADMIN_FEATURE.icon className="h-5 w-5 text-primary" />
                  <span className="font-semibold">{ADMIN_FEATURE.title}</span>
                  <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </div>
                <p className="mb-3 text-sm text-muted-foreground">
                  {ADMIN_FEATURE.tagline}
                </p>
                <ul className="space-y-1.5 text-xs leading-relaxed text-muted-foreground">
                  {ADMIN_FEATURE.points.map((p, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                      {p}
                    </li>
                  ))}
                </ul>
              </Card>
            </Link>
          </div>
        </section>
      )}
    </div>
  );
}
