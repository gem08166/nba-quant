// 主题切换：白昼报表（day，浅冷色）↔ 记分台（dark，默认）。
// 存 localStorage；FOUC 防护：head 里同步脚本在渲染前设置 data-theme。
const THEME_KEY = "nbaq:theme";

const THEME_ICON = {
  dark: '<svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true">'
    + '<circle cx="7.5" cy="7.5" r="3.4" stroke="currentColor" stroke-width="1.5"/>'
    + '<path d="M7.5 1v1.6M7.5 12.4V14M1 7.5h1.6M12.4 7.5H14M2.9 2.9l1.1 1.1M11 11l1.1 1.1M12.1 2.9L11 4M4 11l-1.1 1.1" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>'
    + '</svg>',
  day: '<svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true">'
    + '<path d="M12.2 9.2A5.2 5.2 0 0 1 5.8 2.8a5.4 5.4 0 1 0 6.4 6.4Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>'
    + '</svg>',
};

function themeApply(theme) {
  const root = document.documentElement;
  const next = theme === "day" ? "day" : "dark";
  if (next === "dark") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", "day");
  const icon = document.getElementById("theme-icon");
  if (icon) {
    // 按钮显示"将切换到"的主题图标：暗色时显示太阳（点击去 day），day 时显示月亮
    icon.innerHTML = THEME_ICON[next === "dark" ? "dark" : "day"];
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", next === "day" ? "#f2f4f7" : "#101319");
}

function themeInit() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  themeApply(saved === "day" ? "day" : "dark");
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "day" ? "day" : "dark";
    const next = current === "day" ? "dark" : "day";
    themeApply(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
    // 图表颜色来自 JS 快照——切主题后刷新快照并重绘当前页图表
    window.__refreshChartColors?.();
    if (window.APP_MODE === "static" || window.APP_MODE === "api") {
      const active = document.querySelector("#tabs .tab.active")?.dataset.tab;
      if (active === "bets") window.loadBets?.();
      if (active === "research") window.loadResearch?.();
      if (active === "post" || active === "live") window.loadLive?.();
    }
  });
}

// FOUC 防护：<head> 里同步执行（本文件在 </body> 前加载，首次渲染前生效）
themeInit();
