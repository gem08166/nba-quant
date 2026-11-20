# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

既有实现（非 greenfield）：无框架原生 HTML/CSS/JS 静态站（PWA：manifest + Service Worker），
自托管 IBM Plex 字体，数据来自 `scripts/export_static` 生成的 `data/static.json`
与本地 FastAPI `/api`（127.0.0.1:8000）；部署 GitHub Pages（Actions refresh-and-deploy）。
前端类名与 JS 模板强耦合（约 144 个类，JS 直拼 HTML 字符串），重做展示层必须保持选择器或同步修改 JS。

## Users

单用户（owner 本人）的个人研究工具。场景：PC 浏览器为主、手机 PWA 辅助；
每天比赛日查看赛前信号、下注前核对伤停与价格、赛中看滚球、赛后核对模型预测、
跟踪真实（333体育）与模拟注单的收益与 CLV。

## Product Purpose

把免费数据（ESPN / The Odds API / 333 DOM / hao-tiyu）+ 自建模型
（Elo + 调整后攻防/节奏 + 伤停 impact 闸门 + 分歧/软书/大冷门闸门链）
变成可执行的赛前信号与诚实的复盘：每个下注决策有据可查，长期用 CLV/ROI 验证信号价值。

## Positioning

全链路个人量化闭环：信号 → 闸门过滤 → 下注（333）→ 收盘 CLV → 结算 → 复盘图表。
邻居（比分站/赔率聚合站）只给数据，不给「为什么这条信号可信/不可信」的验收链。

## Operating Context

北京时间口径、全中文界面。333体育真实注单（带券单按 333 netWinAmount 口径）与
模拟单并行跟踪并去重。Windows 常驻进程：FastAPI serve(8000)、watch(15min 采集+推送)、
CL 会话（比赛日收盘捕捉）。GitHub Actions 每日刷新并部署 Pages。

## Capabilities and Constraints

- 5 个 tab：赛前看板 / 赛时 / 赛后复盘 / 收益 / 研究图表；顶部日期驱动全部 tab。
- 免费源约束：The Odds API 2h 一次（额度闸门）；ESPN 30s 延迟（赛时 tab 30s 轮询）。
- PWA 离线壳；双主题（深色默认「记分台」+ 浅色「白昼报表」）带 localStorage 记忆。
- 展示层约束：类名与 JS 强耦合；服务 Worker 缓存版本号需在改动后递增。
- 模型不含伤停输入为已验证盲区，伤停信息以下注前人工核对（Step 1.5）+ impact 闸门补足。

## Brand Commitments

- 名称「NBA Quant」与 Q 方块 logo（SVG，自绘）。
- 「数字是主角」：数字/赔率/比分必须高可读、成排对齐（等宽或 tabular-nums）。
- 文案克制不花哨；颜色只承载状态与方向（赢/输/可下注/警告），不做纯装饰。
- 免责声明固定出现（个人研究、不构成投注建议）。

## Evidence on Hand

- `site/data/static.json`：board / bets / trends / rapm / backtest / model_clv / linemove / holdout。
- 真实台账 101 单（98 已结：47 胜 50 负 1 走，净 −3.42u；3 在台），333 余额 83.81。
- `site/static/*.js`（app/live/bets/research 等 9 个模块，约 230KB）为既有行为事实来源。

## Product Principles

1. 数字第一：任何装饰不得降低数字可读性与对齐精度。
2. 诚实呈现：纸面与真实分开、未结与已结分开、信号与噪声（闸门拦截原因）可见。
3. 颜色承载语义：绿=赢/可下注、红=输、橙=警告、蓝=主操作；其余保持黑白灰。
4. 一次点击到达：从「看到信号」到「333 下单」路径最短。

## Accessibility & Inclusion

- 中文正文对比度 ≥ 4.5:1；数字表格保持高对比。
- 键盘可达（tab/按钮原生元素）；日期与 tab 有 aria 标注（现状已具备，保持）。
