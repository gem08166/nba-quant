// 静态模式（GitHub Pages）的纸面注单：存在本机 localStorage。
// 仅用于个人纸面跟踪；换设备/清缓存会丢，导出接口见 LB.exportJSON()。
//
// 结算口径与后端一致：
//   spread  主队线：净胜分 + 主队让分 > 0 主队赢盘；= 0 走盘
//   total   总分 > 线 大分；= 线 走盘
//   moneyline 净胜分 > 0 主队胜（NBA 无平局）
// 盈亏：赢 = 注额 × (赔率 − 1)，输 = −注额，走 = 0

const LB_KEY = "nbaq:bets:v1";

function lbRead() {
  try {
    const raw = JSON.parse(localStorage.getItem(LB_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch (_) {
    return [];
  }
}

function lbWrite(list) {
  try {
    localStorage.setItem(LB_KEY, JSON.stringify(list));
  } catch (_) {
    /* 隐私模式/配额满：静默失败 */
  }
}

function lbNewId() {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function lbAdd(row) {
  const list = lbRead();
  const bet = {
    id: lbNewId(),
    placed_at: new Date().toISOString(),
    result: "open",
    profit: null,
    clv: null,
    clv_line: null,
    ...row,
  };
  // ref 是跨设备/跨导出的稳定身份：没有就用自己的 id
  bet.ref = String(bet.ref || bet.id);
  list.push(bet);
  lbWrite(list);
  return bet;
}

function lbRemove(id) {
  lbWrite(lbRead().filter((bet) => String(bet.id) !== String(id)));
}

function lbOutcome(bet, game) {
  const hs = game.home_score;
  const as = game.away_score;
  if (hs == null || as == null) return null;
  const margin = Number(hs) - Number(as);
  const total = Number(hs) + Number(as);
  if (bet.market === "moneyline") {
    const homeWon = margin > 0;
    return (bet.side === "home") === homeWon ? "won" : "lost";
  }
  if (bet.market === "spread") {
    const line = Number(bet.line ?? 0);
    const homeLine = bet.side === "home" ? line : -line;
    const diff = margin + homeLine;
    if (Math.abs(diff) < 1e-9) return "push";
    return (bet.side === "home") === (diff > 0) ? "won" : "lost";
  }
  if (bet.market === "total") {
    const line = Number(bet.line ?? 0);
    if (Math.abs(total - line) < 1e-9) return "push";
    return (bet.side === "over") === total > line ? "won" : "lost";
  }
  return null;
}

// 用已完赛比分结算未结算注单（只会处理拿到 final 比分的比赛）
function lbSettle(games) {
  const finals = new Map();
  for (const game of games || []) {
    if (!game) continue;
    const final = game.status === "STATUS_FINAL" || game.status === "final" || game.completed === true;
    if (!final) continue;
    finals.set(String(game.id || game.game_id), game);
  }
  if (!finals.size) return 0;
  const list = lbRead();
  let changed = 0;
  for (const bet of list) {
    if (bet.result !== "open") continue;
    const game = finals.get(String(bet.game_id));
    if (!game) continue;
    const outcome = lbOutcome(bet, game);
    if (!outcome) continue;
    bet.result = outcome;
    bet.profit =
      outcome === "won"
        ? Math.round(bet.stake * (bet.price - 1) * 100) / 100
        : outcome === "lost"
          ? -Math.round(bet.stake * 100) / 100
          : 0;
    changed += 1;
  }
  if (changed) lbWrite(list);
  return changed;
}

function lbKind(bet) {
  // 与后端 settle.bet_kind 同口径：滚球 / 回填 / 赛前
  const note = String((bet && bet.note) || "");
  if (note.includes("滚球")) return "live";
  if (note.includes("回填")) return "backfill";
  return "pregame";
}

// 与后端 settle.bet_source 同口径：回填=模拟，其余=我的真实注单
function lbSource(bet) {
  return lbKind(bet) === "backfill" ? "sim" : "mine";
}

function lbMerge(a, b) {
  const stake = (a?.stake || 0) + (b?.stake || 0);
  const profit = (a?.profit || 0) + (b?.profit || 0);
  const wins = (a?.wins || 0) + (b?.wins || 0);
  const losses = (a?.losses || 0) + (b?.losses || 0);
  return {
    settled: (a?.settled || 0) + (b?.settled || 0),
    open: (a?.open || 0) + (b?.open || 0),
    wins,
    losses,
    pushes: (a?.pushes || 0) + (b?.pushes || 0),
    stake: Math.round(stake * 100) / 100,
    profit: Math.round(profit * 100) / 100,
    roi: stake > 0 ? profit / stake : null,
    hit_rate: wins + losses ? wins / (wins + losses) : null,
    avg_clv: null,
    clv_positive_rate: null,
    avg_clv_line: null,
    clv_line_positive_rate: null,
  };
}

function lbSummary(bets) {  const settled = bets.filter((bet) => bet.result !== "open");
  const wins = settled.filter((bet) => bet.result === "won").length;
  const losses = settled.filter((bet) => bet.result === "lost").length;
  const pushes = settled.filter((bet) => bet.result === "push").length;
  const stake = settled.reduce((sum, bet) => sum + Number(bet.stake || 0), 0);
  const profit = settled.reduce((sum, bet) => sum + Number(bet.profit || 0), 0);
  const roi = stake > 0 ? profit / stake : null;
  const decided = wins + losses;
  const byMarket = {};
  for (const bet of settled) {
    const key = bet.market || "other";
    const bucket = (byMarket[key] = byMarket[key] || { n: 0, stake: 0, profit: 0 });
    bucket.n += 1;
    bucket.stake += Number(bet.stake || 0);
    bucket.profit += Number(bet.profit || 0);
  }
  for (const bucket of Object.values(byMarket)) {
    bucket.roi = bucket.stake > 0 ? bucket.profit / bucket.stake : null;
    bucket.avg_clv = null;
    bucket.avg_clv_line = null;
  }
  const byKind = {};
  for (const kind of ["pregame", "live", "backfill"]) {
    const rows = bets.filter((bet) => lbKind(bet) === kind);
    const settledRows = rows.filter((bet) => bet.result !== "open");
    const kStake = settledRows.reduce((sum, bet) => sum + Number(bet.stake || 0), 0);
    const kProfit = settledRows.reduce((sum, bet) => sum + Number(bet.profit || 0), 0);
    const kWins = settledRows.filter((bet) => bet.result === "won").length;
    const kLosses = settledRows.filter((bet) => bet.result === "lost").length;
    byKind[kind] = {
      settled: settledRows.length,
      open: rows.length - settledRows.length,
      wins: kWins,
      losses: kLosses,
      pushes: settledRows.filter((bet) => bet.result === "push").length,
      stake: Math.round(kStake * 100) / 100,
      profit: Math.round(kProfit * 100) / 100,
      roi: kStake > 0 ? kProfit / kStake : null,
      hit_rate: kWins + kLosses ? kWins / (kWins + kLosses) : null,
      avg_clv: null,
      clv_positive_rate: null,
      avg_clv_line: null,
      clv_line_positive_rate: null,
    };
  }

  return {
    settled: settled.length,
    wins,
    losses,
    pushes,
    stake: Math.round(stake * 100) / 100,
    profit: Math.round(profit * 100) / 100,
    roi,
    hit_rate: decided ? wins / decided : null,
    avg_clv: null,
    clv_positive_rate: null,
    avg_clv_line: null,
    clv_line_positive_rate: null,
    open: bets.length - settled.length,
    by_market: byMarket,
    by_kind: byKind,
    by_source: {
      mine: lbMerge(byKind.pregame, byKind.live),
      sim: byKind.backfill,
    },
  };
}

function lbCurve(bets, normalize = false) {
  const byDate = {};
  for (const bet of bets) {
    if (bet.result === "open") continue;
    const key = bet.slate_date || (bet.placed_at || "").slice(0, 10);
    let step = Number(bet.profit || 0);
    if (normalize) {
      const stake = Number(bet.stake || 0);
      step = stake ? step / stake : 0;
    }
    byDate[key] = (byDate[key] || 0) + step;
  }
  const round = (v) => Math.round(v * (normalize ? 10000 : 100)) / (normalize ? 10000 : 100);
  let cumulative = 0;
  return Object.keys(byDate)
    .sort()
    .map((date) => {
      cumulative += byDate[date];
      return { date, cum_profit: round(cumulative) };
    });
}

function lbAnalysis() {
  const bets = lbRead().map((bet) => ({
    ...bet,
    kind: lbKind(bet),
    source: lbSource(bet),
  }));
  const mine = bets.filter((b) => b.source === "mine");
  const sim = bets.filter((b) => b.source === "sim");
  return {
    summary: lbSummary(bets),
    bets,
    curve: lbCurve(bets),
    curves: { mine: lbCurve(mine), sim: lbCurve(sim) },
    curves_unit: { mine: lbCurve(mine, true), sim: lbCurve(sim, true) },
  };
}

// ---------- 导入/导出：同一注单只落一次 ----------
// 身份优先级：ref → id → 自然键（同场/同市场/同方向/同线/同下注时间）。
// 自然键要归一化：毫秒/时区写法、浮点线（-0.0 / 1e-9）都不能造成"看起来不同"。

function lbRef(row) {
  const ref = row && (row.ref != null ? row.ref : row.id);
  return ref == null ? "" : String(ref).trim();
}

function lbNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n * 1000) / 1000 : null;
}

function lbTime(value) {
  if (!value) return "";
  const t = Date.parse(value);
  return Number.isFinite(t) ? new Date(t).toISOString().slice(0, 19) : String(value).trim();
}

function lbNaturalKey(row) {
  return [
    String(row.game_id || ""),
    String(row.market || "").toLowerCase(),
    String(row.side || "").toLowerCase(),
    lbNum(row.line),
    lbTime(row.placed_at),
  ].join("|");
}

function lbNormalizeRow(row) {
  if (!row || typeof row !== "object") return null;
  if (!row.game_id || !row.market || !row.side) return null;
  const natural = lbNaturalKey(row);
  const ref = lbRef(row) || natural;
  const id = row.id != null && String(row.id).trim() ? row.id : ref;
  return { ...row, id, ref };
}

// 接受裸数组 / {bets:[...]} / {rows:[...]}
function lbImport(payload) {
  let rows = payload;
  if (payload && !Array.isArray(payload) && typeof payload === "object") {
    rows = payload.bets || payload.rows || [];
  }
  const list = lbRead();
  const byRef = new Map();
  const byNatural = new Map();
  const index = (bet) => {
    const ref = lbRef(bet);
    if (ref) byRef.set(ref, bet);
    byNatural.set(lbNaturalKey(bet), bet);
  };
  list.forEach(index);

  let added = 0;
  let updated = 0;
  let skipped = 0;
  let invalid = 0;
  const batch = new Set();

  for (const raw of Array.isArray(rows) ? rows : []) {
    const row = lbNormalizeRow(raw);
    if (!row) {
      invalid += 1;
      continue;
    }
    const natural = lbNaturalKey(row);
    if (batch.has(natural)) {
      skipped += 1; // 同一个文件里重复
      continue;
    }
    batch.add(natural);

    const current = (row.ref && byRef.get(row.ref)) || byNatural.get(natural);
    if (!current) {
      const bet = { ...row, source: row.source || "local" };
      list.push(bet);
      index(bet);
      added += 1;
      continue;
    }

    const curOpen = !current.result || current.result === "open";
    const incoming = row.result;
    const incSettled = incoming && incoming !== "open";
    if (curOpen && incSettled) {
      // 同一注单：以已结算版本为准（云端结算覆盖本地未结算）
      Object.assign(current, { ...row, id: current.id, ref: current.ref || row.ref });
      index(current);
      updated += 1;
      continue;
    }
    if (!curOpen && incSettled && current.result === incoming) {
      // 都已结算：只补空缺的收盘线 / CLV / 盈亏，不改结果
      let filled = false;
      for (const key of ["closing_line", "closing_price", "clv", "clv_line", "profit"]) {
        if (current[key] == null && row[key] != null) {
          current[key] = row[key];
          filled = true;
        }
      }
      if (filled) {
        index(current);
        updated += 1;
      } else {
        skipped += 1;
      }
      continue;
    }
    skipped += 1; // 已结算 ← open：不降级；其余视为重复
  }

  lbWrite(list);
  return { added, updated, skipped, invalid, total: list.length };
}

// 导出前补齐 ref（老数据没有 ref），保证以后重复导入能稳定命中
function lbExportPayload() {
  const list = lbRead().map((bet) => {
    const ref = lbRef(bet) || lbNaturalKey(bet);
    return { ...bet, ref, id: bet.id != null ? bet.id : ref };
  });
  lbWrite(list);
  return {
    schema: "nbaq.bets/1",
    exported_at: new Date().toISOString(),
    count: list.length,
    bets: list,
  };
}

function lbExportJSON() {
  return JSON.stringify(lbExportPayload(), null, 1);
}

window.LB = {
  read: lbRead,
  add: lbAdd,
  remove: lbRemove,
  settle: lbSettle,
  summary: lbSummary,
  curve: lbCurve,
  kind: lbKind,
  source: lbSource,
  analysis: lbAnalysis,
  importRows: lbImport,
  exportPayload: lbExportPayload,
  exportJSON: lbExportJSON,
  davSync: lbDavSync,
};

// 台账 ↔ 坚果云（仅 APK：浏览器直连 WebDAV 会被 CORS 拦）
async function lbDavSync() {
  if (!window.DAV?.davEnabled()) return { skipped: true };
  let mergedIn = 0;
  try {
    const remoteText = await window.DAV.davGetText("nbaq/bets.json");
    if (remoteText) mergedIn = window.LB.importRows(JSON.parse(remoteText)).added;
  } catch (_) {
    /* 云端还没有台账文件 */
  }
  const list = window.LB.read();
  const body = JSON.stringify(list, null, 1);
  const hash = await sha1Hex(body);
  const lastHash = localStorage.getItem("nbaq:dav:ledger-hash");
  let pushed = false;
  if (hash !== lastHash) {
    await window.DAV.davPutText("nbaq/bets.json", body);
    localStorage.setItem("nbaq:dav:ledger-hash", hash);
    pushed = true;
  }
  return { mergedIn, pushed };
}

async function sha1Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-1", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
