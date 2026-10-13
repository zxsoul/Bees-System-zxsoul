"use client";

/**
 * 首页 isometric 动态插画 · 方案 A「中央处理台」
 *
 * 纯 SVG 手绘等距场景 + CSS 动画, 无图片素材:
 * - 员工工位产出的截图/文档, 循环飞入中央 AI 处理台
 * - 处理台核心脉动发光
 * - 机器人把印好的日报往返送往主管高台
 * - 预警灯闪烁, 标记停滞工作项
 * - 主管看板的曲线循环描边
 *
 * 尊重 prefers-reduced-motion: 用户系统开启减弱动效时全部静止。
 */

// ── 等距投影 ─────────────────────────────────────────────────
const S = 24;
function P(x: number, y: number, z = 0): [number, number] {
  return [(x - y) * 0.866 * S, (x + y) * 0.5 * S - z * S];
}
function poly(points: [number, number][], fill: string, extra = "") {
  return `<polygon points="${points.map((p) => p.join(",")).join(" ")}" fill="${fill}" ${extra}/>`;
}
function box(
  x: number, y: number, z: number,
  w: number, d: number, h: number,
  c: { top: string; left: string; right: string }
) {
  const top = [P(x, y, z + h), P(x + w, y, z + h), P(x + w, y + d, z + h), P(x, y + d, z + h)];
  const left = [P(x, y + d, z), P(x + w, y + d, z), P(x + w, y + d, z + h), P(x, y + d, z + h)];
  const right = [P(x + w, y, z), P(x + w, y + d, z), P(x + w, y + d, z + h), P(x + w, y, z + h)];
  return poly(top, c.top) + poly(left, c.left) + poly(right, c.right);
}
function floorTile(x: number, y: number, w: number, d: number, fill: string) {
  return poly([P(x, y), P(x + w, y), P(x + w, y + d), P(x, y + d)], fill);
}
function person(x: number, y: number, shirt: string, opts: { skin?: string; hair?: string; cls?: string } = {}) {
  const [sx, sy] = P(x, y);
  const skin = opts.skin || "#F6C89F";
  const hair = opts.hair || "#3F3D56";
  const body =
    `<ellipse cx="${sx}" cy="${sy}" rx="11" ry="4.5" fill="rgba(0,0,0,0.12)"/>` +
    `<rect x="${sx - 7}" y="${sy - 30}" width="14" height="24" rx="7" fill="${shirt}"/>` +
    `<circle cx="${sx}" cy="${sy - 36}" r="7.5" fill="${skin}"/>` +
    `<path d="M ${sx - 7.5} ${sy - 38} a 7.5 7.5 0 0 1 15 0 l 0 -3 a 7.5 7.5 0 0 0 -15 0 z" fill="${hair}"/>`;
  return opts.cls ? `<g class="${opts.cls}">${body}</g>` : body;
}
function paper(x: number, y: number, z: number, rot = 0, accent = "#6366F1", cls = "") {
  const [sx, sy] = P(x, y, z);
  const w = 26, h = 32;
  const inner =
    `<g transform="translate(${sx},${sy}) rotate(${rot})">` +
    `<rect x="${-w / 2}" y="${-h}" width="${w}" height="${h}" rx="3" fill="#fff" stroke="#E2E8F0"/>` +
    `<rect x="${-w / 2 + 4}" y="${-h + 5}" width="${w - 8}" height="3" rx="1.5" fill="${accent}"/>` +
    `<rect x="${-w / 2 + 4}" y="${-h + 11}" width="${w - 10}" height="2.5" rx="1" fill="#CBD5E1"/>` +
    `<rect x="${-w / 2 + 4}" y="${-h + 16}" width="${w - 8}" height="2.5" rx="1" fill="#CBD5E1"/>` +
    `<rect x="${-w / 2 + 4}" y="${-h + 21}" width="${w - 12}" height="2.5" rx="1" fill="#CBD5E1"/>` +
    `</g>`;
  return cls ? `<g class="${cls}">${inner}</g>` : inner;
}
function beacon(x: number, y: number, color = "#EF4444") {
  const [sx, sy] = P(x, y);
  return (
    `<rect x="${sx - 2}" y="${sy - 34}" width="4" height="34" rx="2" fill="#94A3B8"/>` +
    `<circle class="bc-halo" cx="${sx}" cy="${sy - 40}" r="11" fill="${color}" opacity="0.25"/>` +
    `<circle class="bc-dot" cx="${sx}" cy="${sy - 40}" r="6" fill="${color}"/>`
  );
}
/** 像素风小蜜蜂: 面向右侧, 黄黑条纹 + 扇动翅膀, 可叼一张日报卡 */
function bee(x: number, y: number, z: number, flyCls: string, flipCls: string, carryCls = "") {
  const [sx, sy] = P(x, y, z);
  return (
    `<g class="${flyCls}"><g transform="translate(${sx},${sy}) scale(1.25)"><g class="${flipCls}">` +
    // 翅膀(后层, 扇动)
    `<g class="bee-wing">` +
    `<rect x="-5" y="-13" width="7" height="5" fill="#BAE6FC" opacity="0.9"/>` +
    `<rect x="2" y="-12" width="6" height="4" fill="#E0F2FE" opacity="0.9"/>` +
    `</g>` +
    // 尾刺
    `<rect x="-12" y="-4" width="4" height="3" fill="#292524"/>` +
    // 身体条纹(黄-黑-黄) + 头
    `<rect x="-8" y="-7" width="5" height="9" fill="#FBBF24"/>` +
    `<rect x="-3" y="-7" width="3" height="9" fill="#1F2937"/>` +
    `<rect x="0" y="-7" width="4" height="9" fill="#FBBF24"/>` +
    `<rect x="4" y="-7" width="5" height="9" fill="#292524"/>` +
    // 眼睛 + 触角
    `<rect x="6" y="-5" width="2" height="2" fill="#fff"/>` +
    `<rect x="5" y="-10" width="1.5" height="3" fill="#292524"/>` +
    `<rect x="8" y="-9" width="1.5" height="3" fill="#292524"/>` +
    // 叼着的日报卡(去程可见)
    (carryCls ? `<g class="${carryCls}"><rect x="-5" y="4" width="11" height="8" fill="#fff" stroke="#E2E8F0"/><rect x="-3" y="6" width="7" height="1.6" fill="#22C55E"/><rect x="-3" y="8.6" width="5" height="1.4" fill="#CBD5E1"/></g>` : "") +
    `</g></g></g>`
  );
}
/** 宽对话气泡(可放一句话) */
function bubbleWide(x: number, y: number, z: number, text: string, color = "#4F46E5", cls = "") {
  const [sx, sy] = P(x, y, z);
  const w = text.length * 12.5 + 20;
  const h = 26;
  const inner =
    `<g transform="translate(${sx},${sy})">` +
    `<rect x="${-w / 2}" y="${-h}" width="${w}" height="${h}" rx="9" fill="${color}"/>` +
    `<polygon points="-5,0 5,0 -8,8" fill="${color}"/>` +
    `<text x="0" y="${-h / 2 + 4.5}" text-anchor="middle" font-size="12" font-weight="600" fill="#fff">${text}</text></g>`;
  return cls ? `<g class="${cls}">${inner}</g>` : inner;
}
function magnifier(x: number, y: number, z: number, color = "#4F46E5") {
  const [sx, sy] = P(x, y, z);
  return (
    `<g transform="translate(${sx},${sy}) rotate(-30)">` +
    `<circle cx="0" cy="-14" r="10" fill="rgba(255,255,255,0.55)" stroke="${color}" stroke-width="3.5"/>` +
    `<rect x="-2" y="-4" width="4" height="14" rx="2" fill="${color}"/></g>`
  );
}
function bubble(x: number, y: number, z: number, color = "#6366F1", text = "?", cls = "") {
  const [sx, sy] = P(x, y, z);
  const inner =
    `<g transform="translate(${sx},${sy})">` +
    `<rect x="-14" y="-26" width="28" height="18" rx="8" fill="${color}"/>` +
    `<polygon points="-4,-8 4,-8 -6,0" fill="${color}"/>` +
    `<text x="0" y="-13" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">${text}</text></g>`;
  return cls ? `<g class="${cls}">${inner}</g>` : inner;
}
function dashScreen(x: number, y: number, w = 74, h = 48, accent = "#818CF8") {
  const [sx, sy] = P(x, y);
  return (
    `<g transform="translate(${sx},${sy})">` +
    `<rect x="${-w / 2}" y="${-h}" width="${w}" height="${h}" rx="6" fill="#0F172A"/>` +
    `<rect x="${-w / 2 + 6}" y="${-h + 7}" width="${w * 0.4}" height="5" rx="2.5" fill="${accent}"/>` +
    `<rect x="${-w / 2 + 6}" y="${-h + 17}" width="${w - 12}" height="4" rx="2" fill="#334155"/>` +
    `<rect x="${-w / 2 + 6}" y="${-h + 24}" width="${w - 18}" height="4" rx="2" fill="#334155"/>` +
    `<polyline class="chart-line" points="${-w / 2 + 6},-6 ${-w / 2 + 18},-12 ${-w / 2 + 30},-8 ${-w / 2 + 42},-15 ${-w / 2 + 54},-10"` +
    ` fill="none" stroke="#22D3EE" stroke-width="2" stroke-linecap="round"/></g>`
  );
}
function glow(x: number, y: number, z: number, r: number, color: string, o = 0.35, cls = "") {
  const [sx, sy] = P(x, y, z);
  return `<circle ${cls ? `class="${cls}" ` : ""}cx="${sx}" cy="${sy}" r="${r}" fill="${color}" opacity="${o}"/>`;
}
function emoji(x: number, y: number, z: number, size: number, ch: string, cls = "") {
  const [sx, sy] = P(x, y, z);
  return `<text ${cls ? `class="${cls}" ` : ""}x="${sx}" y="${sy}" text-anchor="middle" font-size="${size}">${ch}</text>`;
}

// ── 动画关键帧(纸张飞行路径需要运行时坐标, 动态生成) ──────────
const PAPERS = [
  { x: 1.5, y: -1.5, z: 2.6, from: P(-3.4, -0.6, 1.6), rot: -12, accent: "#6366F1", delay: 0 },
  { x: 2.6, y: 0.5, z: 3.4, from: P(-3.4, 3.4, 1.6), rot: 8, accent: "#F59E0B", delay: 1.1 },
  { x: 1.2, y: 1.8, z: 2.9, from: P(0.4, -3.0, 1.6), rot: 18, accent: "#22C55E", delay: 2.2 },
].map((p) => ({ ...p, pos: P(p.x, p.y, p.z) }));
/** 三只蜜蜂的航线: 处理台 ⇄ 主管高台, 各有错拍的节奏 */
const BEES = [
  { a: P(6.2, 0.0, 4.8), b: P(10.3, -1.9, 3.4), dur: 6, delay: 0, carry: true },
  { a: P(6.9, 1.5, 4.3), b: P(11.1, -2.7, 3.1), dur: 7.2, delay: 2.4, carry: true },
  { a: P(5.3, 1.9, 4.5), b: P(9.5, -0.5, 3.6), dur: 5.2, delay: 4, carry: false },
];
const rd = (v: number) => Math.round(v * 10) / 10;

const CSS = `
.iso-scene { animation: sceneIn .8s ease-out both; }
@keyframes sceneIn { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }
.bob { animation: bob 3.2s ease-in-out infinite; }
.bob-2 { animation: bob 3.2s ease-in-out .6s infinite; }
.bob-3 { animation: bob 3.2s ease-in-out 1.2s infinite; }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
.core-pulse { transform-box: fill-box; transform-origin: center; animation: corePulse 2.4s ease-in-out infinite; }
@keyframes corePulse { 0%,100% { opacity: .35; transform: scale(1); } 50% { opacity: .65; transform: scale(1.14); } }
.sparkle { transform-box: fill-box; transform-origin: center; animation: spark 2.6s ease-in-out infinite; }
@keyframes spark { 0%,100% { transform: rotate(-10deg) scale(1); } 50% { transform: rotate(10deg) scale(1.18); } }
.bc-halo { transform-box: fill-box; transform-origin: center; animation: bcHalo 1.4s ease-in-out infinite; }
.bc-dot { animation: bcDot 1.4s ease-in-out infinite; }
@keyframes bcHalo { 0%,100% { opacity: .2; transform: scale(1); } 50% { opacity: .55; transform: scale(1.3); } }
@keyframes bcDot { 0%,100% { opacity: 1; } 50% { opacity: .3; } }
.chart-line { stroke-dasharray: 140; stroke-dashoffset: 140; animation: chartDraw 3.6s ease-in-out infinite; }
@keyframes chartDraw { 0% { stroke-dashoffset: 140; opacity: 1; } 55% { stroke-dashoffset: 0; opacity: 1; } 85% { opacity: 1; } 100% { stroke-dashoffset: 0; opacity: .25; } }
.float-bubble { animation: floaty 3s ease-in-out infinite; }
@keyframes floaty { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
.bee-wing { transform-box: fill-box; transform-origin: center bottom; animation: wingFlap .22s steps(2, end) infinite; }
@keyframes wingFlap { 0% { transform: translateY(0); } 100% { transform: translateY(-2.5px); } }
${BEES.map(
  (b, i) => {
    const dx = rd(b.b[0] - b.a[0]);
    const dy = rd(b.b[1] - b.a[1]);
    return `
.bee-fly-${i} { animation: beeFly${i} ${b.dur}s ease-in-out ${b.delay}s infinite; }
@keyframes beeFly${i} {
  0%, 8% { transform: translate(0, 0); }
  27% { transform: translate(${rd(dx * 0.52)}px, ${rd(dy * 0.5 - 26)}px); }
  46%, 54% { transform: translate(${dx}px, ${dy}px); }
  75% { transform: translate(${rd(dx * 0.48)}px, ${rd(dy * 0.5 - 18)}px); }
  96%, 100% { transform: translate(0, 0); }
}
.bee-flip-${i} { transform-box: fill-box; transform-origin: center; animation: beeFlip${i} ${b.dur}s ease-in-out ${b.delay}s infinite; }
@keyframes beeFlip${i} {
  0%, 44% { transform: scaleX(1); }
  50%, 94% { transform: scaleX(-1); }
  100% { transform: scaleX(1); }
}
.carry-${i} { animation: carry${i} ${b.dur}s ease-in-out ${b.delay}s infinite; opacity: 0; }
@keyframes carry${i} {
  0%, 10% { opacity: 0; }
  14%, 42% { opacity: 1; }
  48%, 100% { opacity: 0; }
}`;
  }
).join("")}
${PAPERS.map(
  (p, i) => `
.fly-${i} { animation: fly${i} 3.4s ease-in-out ${p.delay}s infinite; opacity: 0; }
@keyframes fly${i} {
  0% { transform: translate(${rd(p.from[0] - p.pos[0])}px, ${rd(p.from[1] - p.pos[1])}px); opacity: 0; }
  22% { opacity: 1; }
  72% { transform: translate(0, 0); opacity: 1; }
  92%, 100% { transform: translate(0, 0); opacity: 0; }
}`
).join("")}
@media (prefers-reduced-motion: reduce) {
  .iso-scene, .iso-scene * { animation: none !important; }
  .fly-0, .fly-1, .fly-2 { opacity: 1; }
}
`;

// ── 场景拼装 ─────────────────────────────────────────────────
function buildScene(): string {
  const parts: string[] = [];
  // 地板
  parts.push(floorTile(-6, -6, 16, 16, "#EDF0FC"));
  // 左侧三张工位
  const desks: [number, number][] = [[-4, -2], [-4, 2], [-1, -4]];
  desks.forEach(([dx, dy]) => {
    parts.push(box(dx, dy, 0, 2, 1, 0.9, { top: "#FFFFFF", left: "#E4E8F8", right: "#D5DBF2" }));
  });
  // 员工(轻微上下浮动)
  parts.push(person(-3.4, -0.6, "#6366F1", { cls: "bob" }));
  parts.push(person(-3.4, 3.4, "#22C55E", { cls: "bob-2" }));
  parts.push(person(0.4, -3.0, "#F59E0B", { cls: "bob-3" }));
  // 飞向中央的材料(循环动画)
  PAPERS.forEach((p, i) => {
    parts.push(paper(p.x, p.y, p.z, p.rot, p.accent, `fly-${i}`));
  });
  // 中央 AI 处理台
  parts.push(box(4, -1, 0, 3, 3, 2.2, { top: "#EEF2FF", left: "#C7D2FE", right: "#A5B4FC" }));
  parts.push(glow(5.5, 0.5, 3.1, 30, "#818CF8", 0.5, "core-pulse"));
  parts.push(box(5, 0, 2.2, 1, 1, 0.8, { top: "#818CF8", left: "#6366F1", right: "#4F46E5" }));
  parts.push(emoji(5.5, 0.5, 4.3, 30, "✨", "sparkle"));
  // 产出的日报 + 往返送报的像素小蜜蜂(去程叼着日报卡)
  parts.push(paper(7.6, 0.8, 2.6, 10, "#22C55E"));
  BEES.forEach((b, i) => {
    parts.push(bee(
      [6.2, 6.9, 5.3][i], [0.0, 1.5, 1.9][i], [4.8, 4.3, 4.5][i],
      `bee-fly-${i}`, `bee-flip-${i}`, b.carry ? `carry-${i}` : ""
    ));
  });
  // 两端对话气泡: 生成 ⇄ 阅读
  parts.push(bubbleWide(4.0, -1.2, 6.6, "我来帮你生成日报", "#4F46E5", "float-bubble"));
  parts.push(bubbleWide(10.6, -4.6, 5.2, "我来阅读日报", "#0F172A", "float-bubble"));
  // 主管高台 + 看板
  parts.push(box(9, -4, 0, 3.6, 3.6, 1.2, { top: "#FFFFFF", left: "#E4E8F8", right: "#D5DBF2" }));
  parts.push(person(10.0, -1.4, "#0F172A", { hair: "#0F172A" }));
  parts.push(dashScreen(12.2, -3.8));
  // 停滞预警
  parts.push(beacon(9.6, 3.2));
  parts.push(bubble(10.8, 3.6, 2.2, "#EF4444", "3天", "float-bubble"));
  // 搜索 + 问答点缀
  parts.push(magnifier(-1.5, 4.8, 1.2));
  parts.push(bubble(0.6, 5.4, 1.6, "#4F46E5", "问", "float-bubble"));
  return parts.join("");
}

const SCENE = buildScene();

export function IsoHero({ className }: { className?: string }) {
  return (
    <div className={className} aria-hidden="true">
      <style>{CSS}</style>
      <svg
        viewBox="0 0 1040 900"
        className="iso-scene h-full w-full"
        preserveAspectRatio="xMidYMid meet"
      >
        <g
          transform="translate(520 390) scale(1.3)"
          dangerouslySetInnerHTML={{ __html: SCENE }}
        />
      </svg>
    </div>
  );
}
