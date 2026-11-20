# DESIGN.md — NBA Quant · Liquid Glass 设计系统

<!-- impeccable:design-schema 1 -->

生成自：`app/web/styles.css`（2026-11-20 重做版）。选择器与 JS 模板强耦合，改令牌不改变选择器名。

## World

**Liquid Glass（玻璃拟态 2.0，iOS 26 语言）**：数据面板悬浮在流动的彩色光斑底上；
玻璃只做 chrome，数据本体实底。深色（石墨玻璃）为默认主题，浅色（暖白玻璃）为 day 变体。

## Tokens（55 个 CSS 自定义属性，:root 深色 / [data-theme="day"] 浅色）

### 底材
| 令牌 | 深色 | 浅色 | 用途 |
|---|---|---|---|
| `--bg` | #10141c | #eef1f6 | 页底（body 上再合成 4 组彩色光斑 radial + 渐层） |
| `--panel` | rgba(255,255,255,.055) | rgba(255,255,255,.46) | 玻璃 tint |
| `--panel-2/3` | .09/.13 | .62/.78 | 玻璃 hover/增亮层级 |
| `--glass-solid` | #1a2030 | #f7f8fb | reduced-transparency 实底 |
| `--glass-border` | rgba(255,255,255,.14) | rgba(255,255,255,.75) | 玻璃 1px 边 |
| `--rim-top/side/bottom` | 白 .55/.22/.12 | 白 .95/.55 + 墨 .10 | 不对称 specular rim |

### 玻璃配方（.glass-panel/.topbar/.tabs/.stats/.card/.chart-card/.modal-card/.bp-list/.table-wrap/.lm-panel 共享）
1. tint `--panel`
2. `backdrop-filter: blur(16px) saturate(160%)`（-webkit- 同）
3. inset rim：顶 1px 最亮、侧/底递减
4. `::after` sheen：135° 白光渐变（z-index -1）
5. 外阴影 `--glass-shadow` 0 10px 30px

**降级**：`@media (prefers-reduced-transparency: reduce)` → 玻璃转 `--glass-solid` 实底、去 blur/sheen、body 光斑消失。

### 文字
`--text` #e8ebf2 / #1c2530；`--text-dim`；`--muted`。数字一律 `--font-num`（Plex Mono）+ `font-variant-numeric: tabular-nums`。

### 语义色
| 色 | 深色 | 浅色 | 语义 |
|---|---|---|---|
| `--blue`/`--blue-strong` | #4b9af5 / #1e6feb | #1e6feb / #1557c0 | 主操作、可下注（唯一高饱和渐变 `--btn-primary-bg`） |
| `--green` | #3fce77 | #1e8a44 | 赢/命中 |
| `--red` | #f26666 | #c43d3d | 输/落空 |
| `--amber` | #f0a93d | #a8690f | 赛时/滚球/警告 |
| `--paper` | #ece7db | #1c2530(反白) | 最终比分记分纸（保留的旧记忆点） |

### 形状
`--radius` 16px（卡/容器）、`--radius-sm` 11px（控件）、`--radius-xs` 8px（小件）；
胶囊 tabs 15px、modal 20px。阴影双层：外浮 `--glass-shadow` + 内 rim。

## 组件规范

- **topbar**：玻璃横梁，logo 为蓝渐变圆角徽章（13px radius）
- **tabs**：玻璃胶囊分段控件，active = 蓝渐变块 + 蓝光晕阴影；sticky top 8px
- **stats**：一排玻璃读数，`.stat.highlight` 蓝左缘
- **card**：玻璃记分卡；`::before` 左缘状态条（蓝=信号、琥珀=赛时、纸白=复盘）；
  `.bettable` 蓝边 + 蓝内环；`edge-badge.bettable` 蓝底、`.chip-bettable` 蓝渐变小徽章
- **bettable-strip / bettable-banner**：蓝渐变横幅（可下注 = 页面唯一高饱和色块）
- **表格**：实底行 + hover 白 5%；`td.pos` 绿 / `td.neg` 灰
- **lm-panel（滚球记一笔）/ modal / toast**：玻璃浮层，modal 背后 blur(10px) 遮罩
- **按钮**：次级玻璃（panel-2 + blur 8px），primary 蓝渐变 + 蓝光晕

## 主题规则

浅色不是"换个底色"：rim 从"白顶+白侧"翻转为"白顶+墨底"（浅底白 rim 不可见），
tint alpha .055→.46（文字需要实底），语义色全部加深保 4.5:1。
图表色（research.js/bets.js）运行时 `getComputedStyle` 取 `--green/--blue/--red/--amber/--text/--muted/--line-soft`，切主题自动重绘。

## 硬约束（改样式时不可破）

1. 选择器名不动（JS 模板直拼 class）。
2. 数字列必须 tabular-nums + Plex Mono。
3. 玻璃只用于上列表中的容器；新组件做玻璃时必须同时写 reduced-transparency 回退。
4. 可下注 = 蓝渐变，绿色只给结果（赢/命中），不要混用。
5. body 的 4 组光斑 radial 是玻璃折射的"内容"，删掉后玻璃会失效。
