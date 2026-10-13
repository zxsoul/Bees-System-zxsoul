import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * 品牌 Logo —— 蜂巢 Bees（灰色几何蜜蜂）。
 *
 * - 素材: public/brand/bee-grey.png (透明底, 去饱和灰调)
 * - 自适应大小, 通过 ``size`` prop 控制 (单位: px)
 * - withText 时带「蜂巢 BEES」字标
 * - favicon 使用 src/app/icon.png (同一素材), Next.js 自动注入
 */
export function WikforgeLogo({
  size = 32,
  className,
  withText = false,
}: {
  size?: number;
  className?: string;
  withText?: boolean;
}) {
  return (
    <div className={cn("inline-flex items-center gap-2", className)}>
      <Image
        src="/brand/bee-grey.png"
        alt="蜂巢 Bees"
        width={size}
        height={size}
        priority
      />
      {withText && (
        <span className="text-lg font-bold tracking-tight">
          蜂巢
          <span className="ml-1.5 text-[11px] font-semibold tracking-[0.12em] text-muted-foreground">
            BEES
          </span>
        </span>
      )}
    </div>
  );
}
