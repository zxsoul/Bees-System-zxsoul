import Link from "next/link";
import { IsoHero } from "@/components/brand/iso-hero";
import { WikforgeLogo } from "@/components/brand/logo";
import { GuestEntryButton } from "@/components/brand/guest-entry-button";

/**
 * 首页 · 产品门面 (方案 A「中央处理台」动态版)
 *
 * 左侧: 品牌 + 价值主张 + 入口; 右侧: isometric 动态插画,
 * 用一个「员工喂材料 → AI 出日报 → 机器人送报 → 主管看看板」的
 * 小剧场讲清产品闭环。插画为纯 SVG + CSS 动画, 无图片素材。
 */
export default function Home() {
  return (
    <main className="relative flex min-h-screen items-stretch overflow-hidden bg-[#F7F8FE]">
      {/* 左侧文案区 */}
      <div className="relative z-10 flex w-full flex-col justify-center px-8 py-16 sm:px-16 lg:w-[46%] lg:pl-24">
        <div className="mb-14 flex items-center gap-3">
          <WikforgeLogo size={40} />
          <span className="text-[22px] font-bold tracking-tight">Wikforge</span>
        </div>

        <h1 className="mb-5 text-4xl font-bold leading-[1.28] tracking-tight sm:text-5xl">
          汇报这件事，
          <br />
          交给 <span className="text-indigo-600">AI</span> 去跑
        </h1>
        <p className="mb-9 max-w-md text-base leading-relaxed text-muted-foreground">
          员工把截图和文档丢进工作台，AI 自动生成当日日报；
          主管在空闲时扫一眼预警与看板，让汇报日常化。
        </p>

        <div className="mb-12 flex flex-wrap items-start gap-3.5">
          <Link
            href="/dashboard"
            className="rounded-lg bg-indigo-600 px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-indigo-600/30 transition-colors hover:bg-indigo-600/90"
          >
            进入工作台
          </Link>
          <GuestEntryButton />
          <Link
            href="/register"
            className="rounded-lg border border-slate-200 px-7 py-3.5 text-sm font-semibold transition-colors hover:bg-white"
          >
            注册账号
          </Link>
        </div>

        <div className="flex max-w-md flex-wrap gap-2.5">
          {["AI 日报生成", "进度停滞预警", "RAG 问答溯源", "工作留痕"].map((t) => (
            <span
              key={t}
              className="rounded-full border border-slate-200 px-3.5 py-1.5 text-xs text-muted-foreground"
            >
              {t}
            </span>
          ))}
        </div>

        <footer className="mt-16 text-xs text-muted-foreground">
          <a
            href="https://beian.miit.gov.cn/"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
          >
            鲁ICP备2026059905号
          </a>
        </footer>
      </div>

      {/* 右侧动态插画 (小屏隐藏) */}
      <IsoHero className="hidden flex-1 lg:block" />
    </main>
  );
}
