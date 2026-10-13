"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";

/**
 * 首页「游客体验」入口
 * 复用登录页的 guest-login 链路：免注册直接进入演示账号，
 * 让面试官/访客一键体验完整工作台。后端未配置 GUEST_EMAIL 时
 * 给出提示并兜底跳转到登录页。
 */
export function GuestEntryButton() {
  const router = useRouter();
  const guestLogin = useAuthStore((state) => state.guestLogin);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const handleGuestEntry = async () => {
    setError("");
    setIsLoading(true);
    try {
      await guestLogin();
      router.push("/dashboard");
    } catch {
      setError("游客入口暂不可用，请使用账号登录");
      router.push("/login");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <span className="inline-flex flex-col gap-1.5">
      <button
        type="button"
        onClick={handleGuestEntry}
        disabled={isLoading}
        className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-7 py-3.5 text-sm font-semibold text-indigo-600 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        游客体验
        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-500">
          免注册
        </span>
      </button>
      {error && <span className="text-xs text-red-500">{error}</span>}
    </span>
  );
}
