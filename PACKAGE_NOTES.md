# NBA Quant — 打包内容说明

> 说明：本文是**打包时的原始说明**（记录另一台机器 `D:\YYMODEL\nba-quant` 的状态）。
> 2026-11-04 晚已并入 `F:\量化\nba-quant-20261103`，合并过程、本机复核数据与当前运行方式见 `CONTEXT.md` 第 10 节。

打包时间：2026-11-04（北京时间下午）
版本：两轮全量工程优化后（基线 commit `e9fd412`）

---

## 1. 压缩包结构

```
nba-quant/
├── app/                     # 主应用（config/db/api/jobs/models/pricing/web）
├── scripts/                 # 分析与导出脚本（见下表）
├── tests/                   # 46 个测试
├── data/                    # 报告 JSON（不含 nba.db，见第 3 节）
├── site/                    # 可直接部署的静态站产物
├── .github/workflows/       # refresh.yml（Pages 部署 + 全管线）
├── CONTEXT.md               # ★ 交接上下文：全部结论、决策、踩坑、下一步
├── OPTIMIZATION_NOTES.md    # ★ 两轮优化详解 + 完整 git 操作步骤
├── README.md                # 用户文档（第 0 节 = 验证结论）
├── LICENSE                  # MIT
├── pyproject.toml
└── .gitignore
```

## 2. 新增脚本速查

| 脚本 | 用途 | 入口 |
|---|---|---|
| `scripts/holdout_eval.py` | 冻结 holdout 验证：前 70% 训练 → 后 30% 一次性考试（按时间切） | `python -m app.cli holdout --split 0.7` |
| `scripts/blend_sweep.py` | MARKET_BLEND 扫描：选边质量基线（改模型后必跑） | `python -m scripts.blend_sweep` |
| `scripts/linemove_analysis.py` | 信号→线移动方向检验（可下注闸门第 0 层验收） | `python -m app.cli linemove` |
| `scripts/model_clv_history.py` | 模型 vs 收盘线历史检验（已改用共享概率函数） | `python -m scripts.model_clv_history --start 2025-10-21` |
| `scripts/export_static.py` | 静态站导出（含根级 `sw.js`） | `python -m scripts.export_static --out site` |

## 3. 数据库未包含在内

`data/nba.db`（8.9MB，含 1427 场比赛 / 8509 条盘口快照 / 224 条预测 / PBP 54K 行）**不在压缩包里**，原因：

1. 体积大且是二进制，不适合随代码分发
2. 本轮优化已把它移出 Git（改由 GitHub Actions 在独立的 `data` 分支维护）

**取回方式**（二选一）：

- **A. 从 GitHub data 分支**（如果已经推过）：
  ```bash
  git fetch origin data
  git show origin/data:nba.db > data/nba.db
  ```
- **B. 从本地原位置复制**：
  ```
  D:\YYMODEL\nba-quant\data\nba.db  →  解压目录\data\nba.db
  ```

**没有 DB 也能跑**：`pytest` 不依赖 DB；`export_static` 会因缺报告而跳过对应卡片。
重新抓取也可以：`python -m app.cli backfill --start 2025-10-21 --end 2026-11-03`。

## 4. 快速开始

```powershell
cd nba-quant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m pytest -q                    # 期望：46 passed
python -m app.cli status                # 需要 data/nba.db
python -m app.cli serve                 # http://127.0.0.1:8000
```

看静态站（无需后端）：
```powershell
cd site
python -m http.server 8000
```

## 5. 核心结论摘要（详见 CONTEXT.md）

- 模型有真实预测力：walk-forward 分差 MAE 11.92 / 冻结 holdout 12.13（常数基线 14.46）
- 但**相对市场无增量信息**：控制收盘线后 β≈0；线移动顺线比例 30.6%（z=−2.33，显著反向）；blend 扫描显示模型权重伤害选边
- → 信号定位为「模型 vs 市场分歧观察」，**不构成下注依据**
- 系统已内置可下注闸门（bettable）与三层验收仪器，等实时数据积累后复核

## 6. 已知限制

- 本机无 git/gh：所有 commit/push 需自行执行，命令见 `OPTIMIZATION_NOTES.md` 第三节
- 实时盘口快照仅积累 2 天（线移动检验样本不足）
- 收盘线基准目前用单家 `sbr-fanduel`（下一步改 7 家共识）
- 跨季修正只在赛季开局约一周有效，样本 16 场 1 个赛季，明年开季需复核
- 仅为个人研究工具，不提供下注执行或资金通道；请遵守所在地法律
