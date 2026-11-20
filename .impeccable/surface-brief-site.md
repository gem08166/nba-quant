# 方向契约 — NBA Quant 全站重做（A2-4 Liquid Glass 液态玻璃）

日期：2026-11-20 · 状态：用户已选定 A2-4（五版 mockup 对比后拍板）

## THESIS

个人量化研究工具的「专业仪器感」不是靠深色终端，而是靠**一块安静悬浮的玻璃面板**：
数据在玻璃下面流动，需要行动的信号在玻璃上面点亮。
拒绝的类别默认：暗色等宽记分台风（旧版）与普通磨砂玻璃拟态 1.0（2020）。

## OWN-WORLD

- **五层玻璃配方**（iOS 26 Liquid Glass）：tint(白 .42) / blur(14px)+saturate(170%) /
  不对称 specular rim（顶边最强）/ 135° sheen / 彩色光斑底（蓝紫青橙四组 radial，
  固定 attachment，给 blur 供内容）
- **玻璃只做 chrome**：顶栏、tabs（胶囊分段）、统计卡、信号横幅、卡片容器、台账容器、
  按钮（次级半透明玻璃）。**数据本体实底**：赔率格 .34 白、表格行实底、数字纯黑 tabular-nums
- **色板**：强调蓝 #1E6FEB→#3E8EFF 渐变；状态 绿 #1FA05E / 红 #E5484D / 橙 #F2820A；
  紫青橙光斑仅作背景内容不进组件；墨色 #101623
- **字体**：系统栈（-apple-system / PingFang SC / Segoe UI），数字 tabular-nums；
  现有自托管 Plex Mono 保留给赔率/比分列（数字是主角承诺）
- **圆角**：卡 20px / 控件 12px / 小件 7px；阴影双层（外浮 + 内 rim）
- **签名动作**：玻璃面板悬浮在流动彩色底上；「可下注」信号条是唯一的蓝渐变大横幅；
  赔率格 hover 泛起玻璃增亮- **降级纪律**：Safari/Firefox 自动丢 SVG 折射保留磨砂；
  `prefers-reduced-transparency` 下玻璃全部转实底白

## STORY

用户打开页面 3 秒内看到：今天有几场、几条信号可下注、在台几单、赚亏多少。
信号横幅直达下注决策；比赛卡展开三项最优价；台账按日分组结算状态一目了然。

## FIRST VIEWPORT

顶栏玻璃（logo Q + 标题 + 日期 + 主题/清缓存/刷新）→ 胶囊 tabs 五枚（赛前看板 active）
→ 四块统计玻璃卡（场次/信号/在台/盈亏，彩点区分）→ 蓝渐变信号横幅（chips×3）
→ 比赛卡网格 3 列（卡顶队名+时间；模型圆环+让分总分；三列赔率格，可下注格蓝框标记）
→ 台账玻璃面板表。首屏即完整决策链。

## FORM

A2-4 liquid-glass（五选一，用户拍板）。构建路径：**code-first**（Operate 面，
含 mockup 已锁定视觉语言，无 comp round）。类名与 JS 强耦合：重做 CSS/HTML 模板时
保持既有选择器不变（styles.css 换肤路线），JS 模板只在必要时小调。

## CONSTRAINTS（来自 PRODUCT.md，全束）

- 类名-JS 耦合：144 类清单不动；styles.css 整体重写但选择器保留
- 双主题：浅色 A2-4 为主，深色变体从同一令牌派生（data-theme="day" 语义反转）
- PWA：sw.js 缓存版本递增；manifest 图标沿用
- 数字可读性 ≥4.5:1；免责声明保留；中文界面；北京时间口径
- 不动 JS 行为逻辑（app/live/bets/research 230KB），只动展示层与模板类名微调

## FINISH

unreviewed and undocumented is unfinished; this build ends with the finish review,
the verdict, DESIGN.md, and every shipping raster carrying its provenance.
