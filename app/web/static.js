// 真·无后端（静态）模式支持：
// - 页面打开时探测 data/static.json 是否存在 → 存在则为静态模式（GitHub Pages 部署）
// - 静态模式下 api() 的 GET 请求映射到本地 JSON；写操作抛错（只读）
// - 赛时页改为浏览器直连 ESPN（CORS 已验证全开），投影数学在本页 JS 实现
// 由 app.js 的 api() 统一分发，其他模块无感知。

window.APP_MODE = window.APP_MODE || null;
window.STATIC_PAYLOAD = window.STATIC_PAYLOAD || null;

// 静态模式的纸面注单：export 里带的服务端注单（只读）+ 本机 localStorage（可写）。
// 结算用 payload.results（最近 N 天完赛比分）与当天看板兜底。
let lbSettledOnce = false;

function lbAllBets(payload) {
  if (!lbSettledOnce) {
    const games = ((payload && payload.results) || []).concat(((payload && payload.board && payload.board.games) || []));
    window.LB?.settle(games);
    lbSettledOnce = true;
  }
  const server = (((payload && payload.bets && payload.bets.bets) || [])).map((bet) => ({ ...bet, source: "server" }));
  const local = (window.LB?.read() || []).map((bet) => ({ ...bet, source: "local" }));
  return server.concat(local);
}

function lbAllAnalysis(payload) {
  const bets = lbAllBets(payload);
  return { summary: window.LB.summary(bets), bets, curve: window.LB.curve(bets) };
}

async function detectMode() {
  if (window.APP_MODE) return window.APP_MODE;
  try {
    const resp = await fetch("data/static.json", { cache: "no-store" });
    if (resp.ok) {
      window.STATIC_PAYLOAD = await resp.json();
      window.APP_MODE = "static";
      document.body.classList.add("static-mode");
      const brand = document.querySelector(".brand p");
      if (brand) brand.textContent = "静态模式 · 数据由 GitHub Actions 定时更新";
      return "static";
    }
  } catch (_) {
    /* not static */
  }
  window.APP_MODE = "api";
  return "api";
}

// ---------- 静态模式的 API 映射 ----------

function staticApi(path) {
  const payload = window.STATIC_PAYLOAD;
  if (!payload) throw new Error("静态数据未加载");
  const query = path.split("?")[1] || "";
  const params = new URLSearchParams(query);

  if (path.startsWith("/api/games")) {
    // /api/games/{id}：从看板数据构造详情响应（静态导出已附带 odds/injuries）
    const parts = path.split("?")[0].split("/").filter(Boolean);
    if (parts.length >= 3) {
      const gameId = parts[2];
      const game = (payload.board.games || []).find((g) => g.id === gameId);
      if (game) {
        return {
          game,
          odds: game.odds || [],
          injuries: game.injuries || [],
          injury_suggestions: game.injury_suggestions || [],
          bets: lbAllBets(payload).filter(
            (bet) => bet.result === "open" && String(bet.game_id) === String(gameId)
          ),
        };
      }
      throw new Error("比赛不在当前导出的比赛日中");
    }
    return payload.board;
  }
  if (path.startsWith("/api/dates")) {
    return { dates: payload.board.dates || [] };
  }
  if (path.startsWith("/api/bets/summary")) {
    return lbAllAnalysis(payload).summary;
  }
  if (path.startsWith("/api/bets/analysis")) {
    return lbAllAnalysis(payload);
  }
  if (path.startsWith("/api/bets")) {
    const gameId = params.get("game_id");
    const result = params.get("result");
    let bets = lbAllBets(payload);
    if (gameId) bets = bets.filter((b) => String(b.game_id) === String(gameId));
    if (result) bets = bets.filter((b) => b.result === result);
    return { bets };
  }
  if (path.startsWith("/api/backtest")) {
    return { report: payload.backtest.report };
  }
  if (path.startsWith("/api/rapm")) {
    return payload.rapm;
  }
  if (path.startsWith("/api/model-clv")) {
    // 静态导出时已裁剪 model_clv 的形状（无 report 包装），这里补上 API 结构
    const mc = payload.model_clv;
    if (mc && mc.overall) return { report: mc };
    return { report: null };
  }
  if (path.startsWith("/api/linemove")) {
    return payload.linemove || { overall: null };
  }
  if (path.startsWith("/api/holdout")) {
    return payload.holdout || { error: "no holdout report" };
  }
  if (path.startsWith("/api/trends")) {
    return payload.trends;
  }
  if (path.startsWith("/api/model")) {
    return payload.model;
  }
  throw new Error(`静态模式不支持：${path}`);
}

// ---------- 赛时页：ESPN 客户端直连 + 投影数学 ----------

const ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard";
const ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary";
const PERIOD_SECONDS = 720;
const OT_SECONDS = 300;
const REGULATION_SECONDS = 4 * PERIOD_SECONDS;

function jsGameSeconds(period, clockSeconds) {
  period = period || 0;
  const clock = Math.max(clockSeconds || 0, 0);
  if (period <= 4) return (period - 1) * PERIOD_SECONDS + (PERIOD_SECONDS - clock);
  return REGULATION_SECONDS + (period - 5) * OT_SECONDS + (OT_SECONDS - clock);
}

function jsFractionPlayed(period, clockSeconds) {
  return Math.min(Math.max(jsGameSeconds(period, clockSeconds) / REGULATION_SECONDS, 0), 1);
}

// 标准正态分布 CDF（Abramowitz-Stegun 近似，精度足够 UI 用）
function jsNormalCdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const poly =
    t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const cdf = 1 - (Math.exp((-x * x) / 2) / Math.sqrt(2 * Math.PI)) * poly;
  return x >= 0 ? cdf : 1 - cdf;
}

function jsLiveProjection(pregame, period, clockSeconds, currentMargin, currentTotal) {
  const remainingFactor = 1 - jsFractionPlayed(period, clockSeconds);
  const played = jsGameSeconds(period, clockSeconds);
  const sigmaFactor = Math.max(Math.pow((REGULATION_SECONDS - played) / REGULATION_SECONDS, 0.5), 0.05);
  const projMargin = currentMargin + pregame.pred_margin * remainingFactor;
  const projTotal = currentTotal + pregame.pred_total * remainingFactor;
  const sigmaM = Math.max(pregame.sigma_margin * sigmaFactor, 1.0);
  const sigmaT = Math.max(pregame.sigma_total * sigmaFactor, 1.5);
  return {
    proj_margin: projMargin,
    proj_total: projTotal,
    sigma_margin: sigmaM,
    sigma_total: sigmaT,
    home_win_prob: jsNormalCdf(projMargin / sigmaM),
    remaining_factor: remainingFactor,
  };
}

function jsParseEvent(event) {
  const comp = (event.competitions || [{}])[0];
  const competitors = comp.competitors || [];
  const home = competitors.find((c) => c.homeAway === "home");
  const away = competitors.find((c) => c.homeAway === "away");
  if (!home || !away) return null;
  const status = event.status || {};
  const type = status.type || {};
  return {
    game_id: String(event.id),
    state: type.state || "pre",
    status_detail: type.shortDetail || type.detail,
    period: status.period,
    display_clock: status.displayClock,
    clock_seconds: typeof status.clock === "number" ? status.clock : null,
    home_score: parseInt(home.score) || 0,
    away_score: parseInt(away.score) || 0,
  };
}

async function espnLive(date) {
  const ymd = date.replace(/-/g, "");
  const resp = await fetch(`${ESPN_SCOREBOARD}?dates=${ymd}`);
  if (!resp.ok) throw new Error(`ESPN ${resp.status}`);
  const payload = await resp.json();
  const board = (window.STATIC_PAYLOAD || {}).board || { games: [] };
  const games = [];
  for (const event of payload.events || []) {
    const parsed = jsParseEvent(event);
    if (!parsed) continue;
    const boardGame = board.games.find((g) => g.id === parsed.game_id);
    const pred = boardGame ? boardGame.prediction : null;
    const lines = boardGame?.prediction?.lines || {};
    const item = {
      game_id: parsed.game_id,
      matchup: boardGame ? boardGame.matchup : `${parsed.game_id}`,
      home_name: boardGame ? boardGame.home.name : "",
      away_name: boardGame ? boardGame.away.name : "",
      tipoff: boardGame ? boardGame.tipoff : event.date,
      ...parsed,
      pregame: pred
        ? {
            pred_margin: pred.pred_margin,
            pred_total: pred.pred_total,
            sigma_margin: pred.sigma_margin,
            sigma_total: pred.sigma_total,
            home_win_prob: pred.home_win_prob,
          }
        : null,
      lines: lines || {},
    };
    if (parsed.state === "in" && item.pregame) {
      const margin = parsed.home_score - parsed.away_score;
      const total = parsed.home_score + parsed.away_score;
      item.live = jsLiveProjection(item.pregame, parsed.period, parsed.clock_seconds, margin, total);
      const spreadLine = lines.spread;
      const totalLine = lines.total;
      const coverHome =
        spreadLine != null ? jsNormalCdf((item.live.proj_margin + spreadLine) / item.live.sigma_margin) : null;
      item.live_cover = {
        spread_home: coverHome,
        over: totalLine != null ? 1 - jsNormalCdf((totalLine - item.live.proj_total) / item.live.sigma_total) : null,
      };
      // ESPN summary：胜率曲线 + 最近 plays（best-effort）
      try {
        const s = await fetch(`${ESPN_SUMMARY}?event=${parsed.game_id}`);
        if (s.ok) {
          const summary = await s.json();
          const wp = (summary.winprobability || [])
            .map((w) => w.homeWinPercentage)
            .filter((v) => v !== undefined && v !== null);
          if (wp.length) {
            item.espn_home_win_prob = wp[wp.length - 1];
            item.espn_wp_delta = wp[wp.length - 1] - wp[Math.max(0, wp.length - 11)];
          }
          item.recent_plays = (summary.plays || []).slice(-6).map((p) => ({
            clock: (p.clock || {}).displayValue,
            period: (p.period || {}).number,
            text: p.text,
          }));
        }
      } catch (_) {
        /* extras best-effort */
      }
    }
    games.push(item);
  }
  games.sort((a, b) => (a.state === "in" ? -1 : b.state === "in" ? 1 : a.state === "pre" ? -1 : 1));
  return {
    date,
    updated_at: new Date().toISOString(),
    count: games.length,
    games,
  };
}
