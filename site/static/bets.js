// 收益标签页：纸面注单的盈亏汇总、累计收益曲线、分市场拆分、注单台账。
// 依赖 app.js 的 api()/esc()/fmt* 与 research.js 暴露的 window.drawLineChart。

let betsLoaded = false;

const marketLabel = (market) =>
  market === "spread" ? "让分" : market === "total" ? "大小分" : market === "moneyline" ? "胜负" : market;
const sideLabel = (bet) =>
  bet.side === "home" ? "主" : bet.side === "away" ? "客" : bet.side === "over" ? "大" : bet.side === "under" ? "小" : bet.side;
const resultLabel = (result) =>
  ({ won: "胜", lost: "负", push: "走", open: "未结算" }[result] || result);

function resultBadge(result) {
  const cls = result === "won" ? "pos" : result === "lost" ? "neg" : "";
  return `<span class="pill ${result === "won" ? "signal" : ""} ${cls}">${resultLabel(result)}</span>`;
}

const fmtLineMove = (value) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${Number(value).toFixed(1)} 分`;

function renderBetStats(s) {
  const stats = [
    ["已结算", `${s.settled} (胜${s.wins}/负${s.losses}/走${s.pushes})`],
    ["盈亏", `${s.profit >= 0 ? "+" : ""}${s.profit.toFixed(2)}`, s.profit > 0 ? "green" : ""],
    ["ROI", fmtSignedPct(s.roi), s.roi > 0 ? "green" : ""],
    ["命中率", fmtPct(s.hit_rate)],
    ["平均 CLV", fmtSignedPct(s.avg_clv), s.avg_clv > 0 ? "green" : ""],
    ["CLV 为正", fmtPct(s.clv_positive_rate)],
    ["平均线移动", fmtLineMove(s.avg_clv_line), s.avg_clv_line > 0 ? "green" : ""],
    ["线移动为正", fmtPct(s.clv_line_positive_rate)],
    ["未结算", String(s.open)],
  ];
  document.getElementById("bets-stats").innerHTML = stats
    .map(([k, v, cls]) => `<div class="stat"><div class="k">${k}</div><div class="v ${cls || ""}" style="font-size:16px">${esc(v)}</div></div>`)
    .join("");
}

function renderMarkets(byMarket) {
  const entries = Object.entries(byMarket || {});
  const box = document.getElementById("bets-markets");
  if (!entries.length) {
    box.innerHTML = `<div class="inj" style="color:var(--muted)">还没有已结算注单。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>市场</th><th>n</th><th>投入</th><th>盈亏</th><th>ROI</th><th>平均 CLV</th><th>平均线移动</th></tr></thead>
      <tbody>
        ${entries
          .map(([market, s]) => {
            const roi = s.roi != null ? fmtSignedPct(s.roi) : "—";
            const clv = s.avg_clv != null ? fmtSignedPct(s.avg_clv) : "—";
            const clvLine = fmtLineMove(s.avg_clv_line);
            return `<tr>
              <td>${marketLabel(market)}</td>
              <td>${s.n}</td>
              <td>${s.stake.toFixed(2)}</td>
              <td class="${s.profit >= 0 ? "pos" : "neg"}">${s.profit >= 0 ? "+" : ""}${s.profit.toFixed(2)}</td>
              <td class="${s.roi >= 0 ? "pos" : "neg"}">${roi}</td>
              <td>${clv}</td>
              <td>${clvLine}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

function liveBadgeFor(bet) {
  // 未结算注单的实时状态：API 模式用 /api/bets/analysis 带出的 game_status/实时比分；
  // 静态模式（手机）回退到看板导出的 games（兼容旧快照缺字段的情况）。
  if (bet.result !== "open") return "";
  let status = bet.game_status;
  let hs = bet.live_home_score;
  let as = bet.live_away_score;
  if ((status == null || hs == null || as == null) && window.APP_MODE === "static") {
    const game = ((window.STATIC_PAYLOAD?.board || {}).games || []).find(
      (x) => String(x.id) === String(bet.game_id)
    );
    if (game) {
      status = status ?? game.status;
      hs = hs ?? game.live_home_score ?? game.home_score;
      as = as ?? game.live_away_score ?? game.away_score;
    }
  }
  const inProgress = status === "STATUS_IN_PROGRESS" || status === "STATUS_HALFTIME";
  if (!inProgress || hs == null || as == null) return "";
  const margin = hs - as;
  const total = hs + as;
  let text = "";
  if (bet.market === "moneyline") {
    text = (bet.side === "home") === margin > 0 ? "目前领先" : "目前落后";
  } else if (bet.market === "spread") {
    const homeLine = bet.side === "home" ? Number(bet.line ?? 0) : -Number(bet.line ?? 0);
    const diff = margin + homeLine;
    if (Math.abs(diff) < 1e-9) text = "贴盘";
    else text = (bet.side === "home") === diff > 0 ? "目前赢盘" : "目前输盘";
  } else if (bet.market === "total") {
    const line = Number(bet.line ?? 0);
    if (Math.abs(total - line) < 1e-9) text = "贴线";
    else text = (bet.side === "over") === total > line ? "目前赢盘" : "目前输盘";
  }
  return text ? `<span class="pill signal" title="实时比分 ${as} : ${hs}">${text}</span>` : "";
}

function renderLedger(bets) {
  const box = document.getElementById("bets-ledger");
  if (!bets.length) {
    box.innerHTML = `<div class="inj" style="color:var(--muted)">还没有注单。在看板比赛详情里对信号点"记一笔"，或用 <code>POST /api/bets</code>。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>比赛日</th><th>比赛</th><th>市场</th><th>方向</th><th>盘口</th><th>赔率</th><th>注</th><th>模型 EV</th><th>结果</th><th>盈亏</th><th>CLV</th><th>线移动</th></tr></thead>
      <tbody>
        ${bets
          .slice()
          .reverse()
          .map((bet) => {
            const line =
              bet.line == null ? "—" : bet.market === "spread" ? fmtSpread(bet.line) : fmtTotal(bet.line);
            const clv = bet.clv != null ? fmtSignedPct(bet.clv) : "—";
            const clvLine = fmtLineMove(bet.clv_line);
            const ev = bet.ev != null ? fmtSignedPct(bet.ev) : "—";
            const profit =
              bet.result === "open" || bet.profit == null
                ? "—"
                : `<span class="${bet.profit >= 0 ? "pos" : "neg"}">${bet.profit >= 0 ? "+" : ""}${bet.profit.toFixed(2)}</span>`;
            return `<tr>
              <td>${esc(bet.slate_date || "—")}</td>
              <td><button class="linklike" data-goto-game="${esc(bet.game_id)}"
                data-goto-date="${esc(bet.slate_date || "")}"
                data-goto-status="${esc(bet.game_status || "")}"
                title="跳转到该场比赛">${esc(bet.matchup_zh || matchupZh(bet.matchup))}</button></td>
              <td>${marketLabel(bet.market)}</td>
              <td>${sideLabel(bet)}</td>
              <td>${line}</td>
              <td>${fmtPrice(bet.price)}</td>
              <td>${Number(bet.stake).toFixed(2)}</td>
              <td>${ev}</td>
              <td>${resultBadge(bet.result)}${liveBadgeFor(bet)}</td>
              <td>${profit}</td>
              <td class="${bet.clv != null && bet.clv >= 0 ? "pos" : "neg"}">${clv}</td>
              <td class="${bet.clv_line != null && bet.clv_line >= 0 ? "pos" : "neg"}">${clvLine}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

function renderBetsToolbar() {
  const box = document.getElementById("bets-toolbar");
  if (!box) return;
  if (window.APP_MODE !== "static") {
    box.innerHTML = "";
    return;
  }
  const apk = window.DAV?.isApk();
  box.innerHTML = `
    <div class="bets-toolbar">
      <button class="btn small" id="bets-export">导出台账</button>
      <button class="btn small" id="bets-import">导入台账</button>
      ${apk ? `<button class="btn small" id="bets-dav-sync">立即同步</button>
      <button class="btn small" id="bets-dav-settings">同步设置</button>` : ""}
      <input type="file" id="bets-import-file" accept=".json,application/json" hidden>
      ${apk ? `<span class="hint">坚果云自动同步已启用</span>` : `<span class="hint">手机：导出 → 坚果云 app 上传到 nbaq/inbox/；PC 端 30 分钟内自动合并</span>`}
    </div>`;
  document.getElementById("bets-export").addEventListener("click", () => {
    const rows = window.LB.read();
    const blob = new Blob([JSON.stringify(rows, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `nbaq-bets-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  const fileInput = document.getElementById("bets-import-file");
  document.getElementById("bets-import").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      const rows = JSON.parse(await file.text());
      const result = window.LB.importRows(rows);
      toast(`导入完成：新增/更新 ${result.added} 条，共 ${result.total} 条`);
      await loadBets();
    } catch (error) {
      toast(`导入失败：${error.message}`);
    } finally {
      fileInput.value = "";
    }
  });
  if (apk) {
    document.getElementById("bets-dav-sync").addEventListener("click", async () => {
      try {
        const result = await window.LB.davSync();
        toast(`同步完成：拉入 ${result.mergedIn ?? 0} 条${result.pushed ? "，已上传" : ""}`);
        await loadBets();
      } catch (error) {
        toast(`同步失败：${error.message}`);
      }
    });
    document.getElementById("bets-dav-settings").addEventListener("click", openSyncSettings);
  }
}

function openSyncSettings() {
  const cfg = window.DAV.davCfg() || { url: "https://dav.jianguoyun.com/dav/", user: "", pass: "" };
  const oddsKey = localStorage.getItem("nbaq:oddsapi-key") || "";
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.style.zIndex = "60";
  overlay.innerHTML = `
    <div class="modal-card" role="dialog" aria-modal="true">
      <button class="close" aria-label="关闭">×</button>
      <h2>同步设置（仅存本机）</h2>
      <div class="form-grid">
        <div style="grid-column: span 2"><label>WebDAV 地址</label>
          <input id="dav-url" type="text" value="${esc(cfg.url || "https://dav.jianguoyun.com/dav/")}"></div>
        <div><label>坚果云账号（邮箱）</label>
          <input id="dav-user" type="text" value="${esc(cfg.user || "")}" autocomplete="off"></div>
        <div><label>应用密码（非登录密码）</label>
          <input id="dav-pass" type="password" value="${esc(cfg.pass || "")}" autocomplete="off"></div>
        <div style="grid-column: span 2"><label>The Odds API key（可选，抓多书快照用）</label>
          <input id="odds-key" type="text" value="${esc(oddsKey)}" autocomplete="off"></div>
        <div style="grid-column: span 2"><button class="btn primary" id="dav-save">保存</button></div>
      </div>
      <p class="notice" style="border:none;margin-top:10px;padding-top:0">
        应用密码在坚果云网页：账户信息 → 安全选项 → 第三方应用管理 生成。
      </p>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".close").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#dav-save").addEventListener("click", () => {
    window.DAV.davSave({
      url: overlay.querySelector("#dav-url").value.trim() || "https://dav.jianguoyun.com/dav/",
      user: overlay.querySelector("#dav-user").value.trim(),
      pass: overlay.querySelector("#dav-pass").value,
    });
    const odds = overlay.querySelector("#odds-key").value.trim();
    if (odds) localStorage.setItem("nbaq:oddsapi-key", odds);
    else localStorage.removeItem("nbaq:oddsapi-key");
    toast("已保存。同步在下次打开看板/收益页时自动执行");
    overlay.remove();
    loadBets();
  });
}

async function loadBets() {
  try {
    renderBetsToolbar();
    const data = await api("/api/bets/analysis");
    const s = data.summary;
    renderBetStats(s);
    renderMarkets(s.by_market);
    renderLedger(data.bets);

    const canvas = document.getElementById("chart-profit");
    if (data.curve && data.curve.length) {
      window.drawLineChart?.(
        canvas,
        data.curve.map((p) => p.date.slice(2)),
        data.curve.map((p) => p.cum_profit),
        { unit: "" }
      );
    } else {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#8fa1b9";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("暂无已结算注单：结算后这里会出现累计收益曲线", canvas.width / 2, 120);
    }
    betsLoaded = true;
    ensureBetsAutoRefresh();
    document.getElementById("bets-mode-note").textContent =
      window.APP_MODE === "static"
        ? "（静态模式：注单存在本机浏览器 localStorage，换设备或清缓存会丢失；结算在看板更新后自动完成）"
        : "";
  } catch (error) {
    document.getElementById("bets-stats").innerHTML =
      `<div class="empty" style="grid-column:1/-1">收益数据加载失败：${esc(error.message)}</div>`;
  }
}

// 实时台账：收益页可见时每 30 秒静默刷新一次（未结算注单会跟着实时比分
// 显示「目前赢盘/输盘/贴盘」，完赛结算后自动变成胜/负/走）。
let betsAutoTimer = null;
function ensureBetsAutoRefresh() {
  if (betsAutoTimer) return;
  betsAutoTimer = setInterval(() => {
    const panel = document.getElementById("tab-bets");
    if (panel && !panel.hidden && document.visibilityState === "visible") {
      loadBets();
    }
  }, 30000);
  document.addEventListener("visibilitychange", () => {
    const panel = document.getElementById("tab-bets");
    if (document.visibilityState === "visible" && panel && !panel.hidden) loadBets();
  });
}

// 点击台账里的比赛名 → 跳到赛时页（进行中）或赛前看板对应日期并高亮该场
document.getElementById("bets-ledger").addEventListener("click", (event) => {
  const button = event.target.closest("[data-goto-game]");
  if (!button) return;
  window.jumpToGame?.(
    button.dataset.gotoGame,
    button.dataset.gotoDate,
    button.dataset.gotoStatus
  );
});

// 复用统一 tab 分发：切入收益页时加载一次；之后由 30 秒定时器保持新鲜
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button?.dataset.tab === "bets") loadBets();
});
window.loadBets = loadBets;
