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

function renderBetStats(s) {
  const stats = [
    ["已结算", `${s.settled} (胜${s.wins}/负${s.losses}/走${s.pushes})`],
    ["盈亏", `${s.profit >= 0 ? "+" : ""}${s.profit.toFixed(2)}`, s.profit > 0 ? "green" : ""],
    ["ROI", fmtSignedPct(s.roi), s.roi > 0 ? "green" : ""],
    ["命中率", fmtPct(s.hit_rate)],
    ["平均 CLV", fmtSignedPct(s.avg_clv), s.avg_clv > 0 ? "green" : ""],
    ["CLV 为正", fmtPct(s.clv_positive_rate)],
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
      <thead><tr><th>市场</th><th>n</th><th>投入</th><th>盈亏</th><th>ROI</th><th>平均 CLV</th></tr></thead>
      <tbody>
        ${entries
          .map(([market, s]) => {
            const roi = s.roi != null ? fmtSignedPct(s.roi) : "—";
            const clv = s.avg_clv != null ? fmtSignedPct(s.avg_clv) : "—";
            return `<tr>
              <td>${marketLabel(market)}</td>
              <td>${s.n}</td>
              <td>${s.stake.toFixed(2)}</td>
              <td class="${s.profit >= 0 ? "pos" : "neg"}">${s.profit >= 0 ? "+" : ""}${s.profit.toFixed(2)}</td>
              <td class="${s.roi >= 0 ? "pos" : "neg"}">${roi}</td>
              <td>${clv}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

function renderLedger(bets) {
  const box = document.getElementById("bets-ledger");
  if (!bets.length) {
    box.innerHTML = `<div class="inj" style="color:var(--muted)">还没有注单。在看板比赛详情里对信号点"记一笔"，或用 <code>POST /api/bets</code>。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>比赛日</th><th>比赛</th><th>市场</th><th>方向</th><th>盘口</th><th>赔率</th><th>注</th><th>模型 EV</th><th>结果</th><th>盈亏</th><th>CLV</th></tr></thead>
      <tbody>
        ${bets
          .slice()
          .reverse()
          .map((bet) => {
            const line =
              bet.line == null ? "—" : bet.market === "spread" ? fmtSpread(bet.line) : fmtTotal(bet.line);
            const clv = bet.clv != null ? fmtSignedPct(bet.clv) : "—";
            const ev = bet.ev != null ? fmtSignedPct(bet.ev) : "—";
            const profit =
              bet.result === "open" || bet.profit == null
                ? "—"
                : `<span class="${bet.profit >= 0 ? "pos" : "neg"}">${bet.profit >= 0 ? "+" : ""}${bet.profit.toFixed(2)}</span>`;
            return `<tr>
              <td>${esc(bet.slate_date || "—")}</td>
              <td>${esc(bet.matchup)}</td>
              <td>${marketLabel(bet.market)}</td>
              <td>${sideLabel(bet)}</td>
              <td>${line}</td>
              <td>${fmtPrice(bet.price)}</td>
              <td>${Number(bet.stake).toFixed(2)}</td>
              <td>${ev}</td>
              <td>${resultBadge(bet.result)}</td>
              <td>${profit}</td>
              <td class="${bet.clv != null && bet.clv >= 0 ? "pos" : "neg"}">${clv}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

async function loadBets() {
  try {
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
  } catch (error) {
    document.getElementById("bets-stats").innerHTML =
      `<div class="empty" style="grid-column:1/-1">收益数据加载失败：${esc(error.message)}</div>`;
  }
}

// 复用统一 tab 分发：切入收益页时加载一次；注单变化后重进页面即刷新
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button?.dataset.tab === "bets") loadBets();
});
window.loadBets = loadBets;
