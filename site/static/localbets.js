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

function lbSummary(bets) {
  const settled = bets.filter((bet) => bet.result !== "open");
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
  };
}

function lbCurve(bets) {
  const byDate = {};
  for (const bet of bets) {
    if (bet.result === "open") continue;
    const key = bet.slate_date || (bet.placed_at || "").slice(0, 10);
    byDate[key] = (byDate[key] || 0) + Number(bet.profit || 0);
  }
  let cumulative = 0;
  return Object.keys(byDate)
    .sort()
    .map((date) => {
      cumulative += byDate[date];
      return { date, cum_profit: Math.round(cumulative * 100) / 100 };
    });
}

function lbAnalysis() {
  const bets = lbRead();
  return { summary: lbSummary(bets), bets, curve: lbCurve(bets) };
}

function lbImport(rows) {
  const list = lbRead();
  const byId = new Map(list.map((bet) => [String(bet.id), bet]));
  let added = 0;
  for (const row of rows || []) {
    if (!row || !row.id) continue;
    const key = String(row.id);
    const current = byId.get(key);
    if (!current) {
      list.push({ ...row, source: "local" });
      byId.set(key, row);
      added += 1;
    } else if (current.result === "open" && row.result && row.result !== "open") {
      // 同一注单：以已结算版本为准（云端结算结果覆盖本地未结算）
      Object.assign(current, row);
      added += 1;
    }
  }
  lbWrite(list);
  return { added, total: list.length };
}

window.LB = {
  read: lbRead,
  add: lbAdd,
  remove: lbRemove,
  settle: lbSettle,
  summary: lbSummary,
  curve: lbCurve,
  analysis: lbAnalysis,
  importRows: lbImport,
};
