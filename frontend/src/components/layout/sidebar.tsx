"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  Search,
  MessageSquare,
  Settings,
  FolderTree,
  LayoutDashboard,
  ClipboardList,
  BellRing,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useAuthStore } from "@/stores/auth-store";
import { WikforgeLogo } from "@/components/brand/logo";

/**
 * 侧边栏 · 方案 A「双端分组」
 *
 * 四个核心功能（概览 / 今日工作台 / 进度中心 / 进度问答）按
 * 员工端 / 主管端分组，大卡片 + 一句话说明；其余功能降级为
 * 「工具」小字列表。配色克制在白 + 靛紫单色内。
 */

const overviewItem = {
  title: "概览",
  href: "/dashboard",
  icon: LayoutDashboard,
};

const employeeItems = [
  {
    title: "今日工作台",
    desc: "截图、日志、文档丢进来，AI 一键生成当日日报",
    href: "/workbench",
    icon: ClipboardList,
  },
];

const managerItems = [
  {
    title: "进度中心",
    desc: "停滞预警 + 全员日报一览，风险自动浮出水面",
    href: "/alerts",
    icon: BellRing,
  },
  {
    title: "进度问答",
    desc: "用自然语言追问任何细节，答案附引用可溯源",
    href: "/chat",
    icon: MessageSquare,
  },
];

const toolItems = [
  { title: "工作材料", href: "/documents", icon: FileText },
  { title: "团队空间", href: "/spaces", icon: FolderTree },
  { title: "搜索", href: "/search", icon: Search },
];

const adminItems = [{ title: "系统管理", href: "/admin", icon: Settings }];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 mt-5 flex items-center gap-2 px-1 first:mt-1">
      <span className="whitespace-nowrap text-[11px] font-semibold tracking-[0.12em] text-muted-foreground">
        {children}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

function FeatureCard({
  item,
  isActive,
}: {
  item: { title: string; desc: string; href: string; icon: React.ElementType };
  isActive: boolean;
}) {
  return (
    <Link
      href={item.href}
      className={cn(
        "mb-2 flex gap-3 rounded-xl border p-3 transition-all",
        isActive
          ? "border-indigo-200 bg-indigo-50/70 shadow-sm dark:border-indigo-800 dark:bg-indigo-950/40"
          : "border-border bg-card hover:-translate-y-px hover:border-indigo-200 hover:shadow-md hover:shadow-indigo-600/5 dark:hover:border-indigo-800"
      )}
    >
      <div
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
          isActive
            ? "bg-indigo-600 text-white"
            : "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400"
        )}
      >
        <item.icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <span className="block text-sm font-semibold leading-tight">
          {item.title}
        </span>
        <span className="mt-1 block text-xs leading-snug text-muted-foreground">
          {item.desc}
        </span>
      </div>
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const { isCollapsed, toggle } = useSidebarStore();
  const user = useAuthStore((state) => state.user);

  // 收起态：全部退化为图标 + 悬停提示
  const collapsedItems = [
    overviewItem,
    ...employeeItems,
    ...managerItems,
    ...toolItems,
    ...(user?.is_admin ? adminItems : []),
  ];

  return (
    <TooltipProvider delayDuration={0}>
      <aside
        className={cn(
          "relative flex h-full flex-col border-r bg-sidebar transition-all duration-300",
          isCollapsed ? "w-16" : "w-72"
        )}
      >
        {/* Logo */}
        <div className="flex h-14 items-center border-b px-4">
          {isCollapsed ? (
            <Link href="/" className="mx-auto" aria-label="Wikforge">
              <WikforgeLogo size={28} />
            </Link>
          ) : (
            <Link href="/" className="flex items-center gap-2">
              <WikforgeLogo size={28} />
              <span className="text-lg font-bold tracking-tight text-sidebar-foreground">
                Wikforge
              </span>
            </Link>
          )}
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-8 w-8 text-sidebar-foreground",
              isCollapsed ? "mx-auto" : "ml-auto"
            )}
            onClick={toggle}
            aria-label={isCollapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            {isCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </Button>
        </div>

        {/* Navigation */}
        <ScrollArea className="flex-1 px-3 py-4">
          {isCollapsed ? (
            <nav className="flex flex-col gap-1">
              {collapsedItems.map((item) => {
                const isActive = pathname.startsWith(item.href);
                return (
                  <Tooltip key={item.href}>
                    <TooltipTrigger asChild>
                      <Link
                        href={item.href}
                        className={cn(
                          "flex items-center justify-center rounded-md px-2 py-2 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                        )}
                      >
                        <item.icon className="h-4 w-4 shrink-0" />
                      </Link>
                    </TooltipTrigger>
                    <TooltipContent side="right">{item.title}</TooltipContent>
                  </Tooltip>
                );
              })}
            </nav>
          ) : (
            <nav>
              {/* 首页入口 */}
              <Link
                href={overviewItem.href}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors",
                  pathname.startsWith(overviewItem.href)
                    ? "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/50 dark:text-indigo-400"
                    : "text-sidebar-foreground hover:bg-sidebar-accent"
                )}
              >
                <LayoutDashboard className="h-4 w-4" />
                概览
              </Link>

              <SectionLabel>员工端 · 每天几分钟</SectionLabel>
              {employeeItems.map((item) => (
                <FeatureCard
                  key={item.href}
                  item={item}
                  isActive={pathname.startsWith(item.href)}
                />
              ))}

              <SectionLabel>主管端 · 不开会掌握进展</SectionLabel>
              {managerItems.map((item) => (
                <FeatureCard
                  key={item.href}
                  item={item}
                  isActive={pathname.startsWith(item.href)}
                />
              ))}

              <SectionLabel>工具</SectionLabel>
              <div className="flex flex-col">
                {[...toolItems, ...(user?.is_admin ? adminItems : [])].map(
                  (item) => {
                    const isActive = pathname.startsWith(item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={cn(
                          "flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors",
                          isActive
                            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                            : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                        )}
                      >
                        <item.icon className="h-3.5 w-3.5 shrink-0" />
                        {item.title}
                      </Link>
                    );
                  }
                )}
              </div>
            </nav>
          )}
        </ScrollArea>
      </aside>
    </TooltipProvider>
  );
}
