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
  // 数据通道优先级（APK）：坚果云 static.json（国内网络稳）→ github.io 兜底
  if (window.DAV?.davEnabled()) {
    try {
      const remote = await window.DAV.davGetText("nbaq/static.json");
      if (remote) {
        window.STATIC_PAYLOAD = JSON.parse(remote);
        window.APP_MODE = "static";
        window.STATIC_SOURCE = "webdav";
        document.body.classList.add("static-mode");
        const brand = document.querySelector(".brand p");
        if (brand) brand.textContent = "本机模式 · 数据经坚果云同步";
        return "static";
      }
    } catch (_) {
      /* 坚果云不可达时回落到 Pages */
    }
  }
  try {
    const resp = await fetch("data/static.json", { cache: "no-store" });
    if (resp.ok) {
      window.STATIC_PAYLOAD = await resp.json();
      window.APP_MODE = "static";
      window.STATIC_SOURCE = "pages";
      document.body.classList.add("static-mode");
      const brand = document.querySelector(".brand p");
      if (brand) {
        brand.textContent = window.DAV?.isApk()
          ? "本机模式 · 数据来自 GitHub Pages"
          : "静态模式 · 数据由 GitHub Actions 定时更新";
      }
      return "static";
    }
  } catch (_) {
    /* not static */
  }
  window.APP_MODE = "api";
  return "api";
}

// 刷新数据通道（静态模式点「刷新」时调用；有新快照才更新，避免无谓重渲染）
async function refreshStaticPayload() {
  if (window.APP_MODE !== "static") return "api";
  if (window.DAV?.davEnabled()) {
    try {
      const remote = await window.DAV.davGetText("nbaq/static.json");
      if (remote) {
        const parsed = JSON.parse(remote);
        const old = window.STATIC_PAYLOAD?.generated_at;
        window.STATIC_PAYLOAD = parsed;
        window.STATIC_SOURCE = "webdav";
        return parsed.generated_at === old ? "webdav-same" : "webdav-updated";
      }
    } catch (_) {
      /* 坚果云不可达 → 回落 Pages */
    }
  }
  try {
    const resp = await fetch("data/static.json", { cache: "no-store" });
    if (resp.ok) {
      const parsed = await resp.json();
      const old = window.STATIC_PAYLOAD?.generated_at;
      window.STATIC_PAYLOAD = parsed;
      window.STATIC_SOURCE = "pages";
      return parsed.generated_at === old ? "pages-same" : "pages-updated";
    }
  } catch (_) {
    /* ignore */
  }
  return "unavailable";
}
window.refreshStaticPayload = refreshStaticPayload;

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
  if (path.startsWith("/api/live-wp")) {
    // 静态模式只有验收报告，没有逐场轨迹（轨迹来自本机 watch 的实时记录）
    const lw = payload.live_wp || null;
    return { report: lw, params: (lw && lw.params) || null, points: [], count: 0 };
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

function jsLinescores(competitor) {
  return (competitor.linescores || []).map((item) => {
    const v = item.value ?? item.displayValue;
    const n = parseInt(v);
    return Number.isFinite(n) ? n : null;
  });
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
    home_linescores: jsLinescores(home),
    away_linescores: jsLinescores(away),
  };
}


// ---- 赛况逐条翻译（静态模式用；API 模式由后端返回 text_zh） ----
const PLAY_TEAM_ZH = {
  hawks: "老鹰", celtics: "凯尔特人", nets: "篮网", hornets: "黄蜂", bulls: "公牛",
  cavaliers: "骑士", mavericks: "独行侠", nuggets: "掘金", pistons: "活塞",
  warriors: "勇士", rockets: "火箭", pacers: "步行者", clippers: "快船",
  lakers: "湖人", grizzlies: "灰熊", heat: "热火", bucks: "雄鹿",
  timberwolves: "森林狼", pelicans: "鹈鹕", knicks: "尼克斯", thunder: "雷霆",
  magic: "魔术", "76ers": "76人", suns: "太阳", "trail blazers": "开拓者",
  blazers: "开拓者", kings: "国王", spurs: "马刺", raptors: "猛龙", jazz: "爵士",
  wizards: "奇才",
};
const PLAY_ZH_RULES = [
  [/makes (\d+)-foot three point jumper/gi, "三分命中（$1英尺）"],
  [/misses (\d+)-foot three point jumper/gi, "三分不中（$1英尺）"],
  [/makes three point jumper/gi, "三分命中"],
  [/misses three point jumper/gi, "三分不中"],
  [/makes (\d+)-foot (?:two point )?jumper/gi, "跳投命中（$1英尺）"],
  [/misses (\d+)-foot (?:two point )?jumper/gi, "跳投不中（$1英尺）"],
  [/makes (?:two point )?jumper/gi, "跳投命中"],
  [/misses (?:two point )?jumper/gi, "跳投不中"],
  [/makes (\d+)-foot [\w\s]*jump shot/gi, "跳投命中（$1英尺）"],
  [/misses (\d+)-foot [\w\s]*jump shot/gi, "跳投不中（$1英尺）"],
  [/makes [\w\s]*jump shot/gi, "跳投命中"],
  [/misses [\w\s]*jump shot/gi, "跳投不中"],
  [/makes (\d+)-foot [\w\s]*layup/gi, "上篮得分"],
  [/misses (\d+)-foot [\w\s]*layup/gi, "上篮不中"],
  [/makes [\w\s]*layup/gi, "上篮得分"],
  [/misses [\w\s]*layup/gi, "上篮不中"],
  [/makes (\d+)-foot layup/gi, "上篮得分"],
  [/misses (\d+)-foot layup/gi, "上篮不中"],
  [/makes layup/gi, "上篮得分"],
  [/misses layup/gi, "上篮不中"],
  [/makes (\d+)-foot dunk/gi, "扣篮得分"],
  [/misses (\d+)-foot dunk/gi, "扣篮不中"],
  [/makes (?:slam )?dunk/gi, "扣篮得分"],
  [/misses (?:slam )?dunk/gi, "扣篮不中"],
  [/makes (\d+)-foot hook shot/gi, "勾手命中"],
  [/misses (\d+)-foot hook shot/gi, "勾手不中"],
  [/makes hook shot/gi, "勾手命中"],
  [/misses hook shot/gi, "勾手不中"],
  [/makes (\d+)-foot (?:two point )?shot/gi, "投篮命中（$1英尺）"],
  [/misses (\d+)-foot (?:two point )?shot/gi, "投篮不中（$1英尺）"],
  [/makes (?:two point )?shot/gi, "投篮命中"],
  [/misses (?:two point )?shot/gi, "投篮不中"],
  [/makes free throw (\d+) of (\d+)/gi, "罚球 $1/$2 命中"],
  [/misses free throw (\d+) of (\d+)/gi, "罚球 $1/$2 不中"],
  [/defensive team rebound/gi, "球队防守篮板"],
  [/offensive team rebound/gi, "球队前场篮板"],
  [/defensive rebound/gi, "防守篮板"],
  [/offensive rebound/gi, "前场篮板"],
  [/team rebound/gi, "球队篮板"],
  [/personal foul/gi, "个人犯规"],
  [/shooting foul/gi, "投篮犯规"],
  [/loose ball foul/gi, "无球犯规"],
  [/offensive foul/gi, "进攻犯规"],
  [/technical foul/gi, "技术犯规"],
  [/coach'?s challenge/gi, "教练挑战"],
  [/call overturned/gi, "改判成功"],
  [/retain their timeout/gi, "保留暂停"],
  [/enters the game/gi, "上场"],
  [/turnover/gi, "失误"],
  [/steal/gi, "抢断"],
  [/block/gi, "盖帽"],
  [/timeout/gi, "暂停"],
  [/jump ball/gi, "跳球"],
];
const PLAY_NICK_RE = new RegExp(
  "\\b(" +
    Object.keys(PLAY_TEAM_ZH)
      .sort((a, b) => b.length - a.length)
      .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|") +
    ")\\b",
  "gi"
);

let PLAYER_ZH_MAP = null;
let PLAYER_ZH_LOADING = null;

function ensurePlayerZh() {
  if (PLAYER_ZH_MAP) return Promise.resolve(PLAYER_ZH_MAP);
  if (!PLAYER_ZH_LOADING) {
    PLAYER_ZH_LOADING = fetch("data/player_zh.json")
      .then((r) => (r.ok ? r.json() : { players: {} }))
      .then((j) => {
        PLAYER_ZH_MAP = j.players || {};
        return PLAYER_ZH_MAP;
      })
      .catch(() => {
        PLAYER_ZH_MAP = {};
        return PLAYER_ZH_MAP;
      });
  }
  return PLAYER_ZH_LOADING;
}

function slugifyZh(name) {
  let text = String(name || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[.'’\-]/g, " ")
    .toLowerCase();
  const parts = text.split(/\s+/).filter(Boolean);
  while (parts.length > 2 && ["jr", "sr", "ii", "iii", "iv", "v"].includes(parts[parts.length - 1])) {
    parts.pop();
  }
  return parts.join("").replace(/[^a-z0-9]/g, "");
}

function translatePlayerPrefixZh(text, map) {
  const m = text.match(/^([A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,3})\s+(.*)$/);
  if (!m) return text;
  const words = m[1].split(/\s+/);
  for (let take = words.length; take >= 1; take--) {
    const candidate = words.slice(0, take).join(" ");
    const zh = map[slugifyZh(candidate)];
    if (zh) {
      const tail = words.slice(take).join(" ");
      return `${zh} ${tail} ${m[2]}`.replace(/\s+/g, " ").trim();
    }
  }
  return text;
}

function playZh(text) {
  if (!text) return "";
  let out = translatePlayerPrefixZh(String(text).trim(), PLAYER_ZH_MAP || {});
  out = out.replace(PLAY_NICK_RE, (m) => PLAY_TEAM_ZH[m.toLowerCase()] || m);
  for (const [re, repl] of PLAY_ZH_RULES) out = out.replace(re, repl);
  return out.replace(/\s+/g, " ").trim();
}

async function espnLive(date) {
  const ymd = date.replace(/-/g, "");
  const resp = await fetch(`${ESPN_SCOREBOARD}?dates=${ymd}`);
  if (!resp.ok) throw new Error(`ESPN ${resp.status}`);
  const payload = await resp.json();
  const board = (window.STATIC_PAYLOAD || {}).board || { games: [] };
  // 历史复盘包：静态导出带上最近若干比赛日的完整数据（预测/盘口/信号历史）
  const history = (window.STATIC_PAYLOAD || {}).history || {};
  const histGames = history[date] || [];
  const histById = new Map(histGames.map((g) => [String(g.game_id), g]));
  const games = [];
  for (const event of payload.events || []) {
    const parsed = jsParseEvent(event);
    if (!parsed) continue;
    const boardGame = board.games.find((g) => g.id === parsed.game_id);
    const histGame = histById.get(String(parsed.game_id));
    const pred = boardGame ? boardGame.prediction : null;
    const lines = boardGame?.prediction?.lines || {};
    const item = {
      game_id: parsed.game_id,
      matchup: boardGame ? boardGame.matchup : `${parsed.game_id}`,
      matchup_zh: boardGame ? (boardGame.matchup_zh || matchupZh(boardGame.matchup)) : "",
      home_name: boardGame ? boardGame.home.name : "",
      away_name: boardGame ? boardGame.away.name : "",
      home_name_zh: boardGame ? (boardGame.home.name_zh || teamNameZh(boardGame.home.name, boardGame.home.abbrev)) : "",
      away_name_zh: boardGame ? (boardGame.away.name_zh || teamNameZh(boardGame.away.name, boardGame.away.abbrev)) : "",
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
    // 历史复盘包优先（往期比赛日的赛前预测 + 信号历史）
    if (histGame) {
      item.matchup = histGame.matchup || item.matchup;
      item.matchup_zh = histGame.matchup_zh || item.matchup_zh;
      item.home_name = histGame.home_name || item.home_name;
      item.away_name = histGame.away_name || item.away_name;
      item.home_name_zh = histGame.home_name_zh || item.home_name_zh;
      item.away_name_zh = histGame.away_name_zh || item.away_name_zh;
      item.tipoff = histGame.tipoff || item.tipoff;
      if (histGame.pregame) item.pregame = histGame.pregame;
      if (histGame.lines && Object.keys(histGame.lines).length) item.lines = histGame.lines;
      item.signals_history = histGame.signals_history || [];
    } else if (parsed.state === "post" && boardGame?.prediction?.edges) {
      // 静态模式：用导出快照的 edges 近似"该场出现过的信号"（命中结果由 live.js 结算）
      item.signals_history = boardGame.prediction.edges
        .filter((e) => e.signal)
        .map((e) => ({
          market: e.market,
          side: e.side,
          line: e.line ?? null,
          price_best: e.price,
          ev_max: e.ev ?? 0,
          bettable_ever: !!e.bettable,
          gated_ever: !!e.gated,
        }));
    }
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
          await ensurePlayerZh();
          item.recent_plays = (summary.plays || []).slice(-6).map((p) => ({
            clock: (p.clock || {}).displayValue,
            period: (p.period || {}).number,
            text: p.text,
            text_zh: playZh(p.text),
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
