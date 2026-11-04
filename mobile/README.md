# NBA Quant 手机壳（Capacitor APK）

把 `../site`（静态站）包成 Android APK。APK 里带 Capacitor 原生网络桥
（CapacitorHttp），所以手机上可以直连坚果云 WebDAV（浏览器里会被 CORS 拦）。

手机端能力：
- 看板/收益/研究页（数据优先从坚果云 `nbaq/static.json` 拉，github.io 兜底）
- 纸面注单：本地记录 + 坚果云自动双向同步（`nbaq/bets.json`）
- 「抓快照」：ESPN（+可选 The Odds API）采集盘口快照 → `nbaq/snapshots/inbox/`，
  PC 每 30 分钟合并入库
- 设置：坚果云账号/应用密码/The Odds API key（只存手机本机）

## 构建步骤（build.ps1 一键完成）

1. 首次：装依赖 → 生成 android 工程 → gradle 打 debug APK
2. 之后：只需重跑（会重新拷贝最新 site/ 并同步）

产物：`mobile\nbaq-debug.apk`（调试签名，直接安装）

```powershell
cd mobile
.\build.ps1              # 首次或每次更新
.\build.ps1 -SkipWeb     # 只重打包，不重新拷贝 www
```

## 环境要求

- Node.js ≥ 20、JDK 17（winget 已装）
- Android SDK：`%LOCALAPPDATA%\Android\Sdk`（已有；缺的 platform gradle 会自动下）

## 签名发布版（可选）

debug 签名只适合自己装。要长期用，生成 keystore 后改 `android/app/build.gradle`
的 signingConfigs，或用 `.\gradlew assembleRelease` + 环境变量方式签名。
