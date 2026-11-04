// WebDAV 客户端（坚果云）——仅在 APK（Capacitor 原生网络）下可用。
// 浏览器里被 CORS 拦死，所以所有调用前先检查 isApk()。
// 配置存 localStorage（账号 + 坚果云应用密码）。

const DAV_KEY = "nbaq:dav";

// 出厂默认（个人 APK 内置，手机开箱即用；设置界面可覆盖，存 localStorage）
const DAV_DEFAULTS = {
  url: "https://dav.jianguoyun.com/dav/",
  user: "13622913166@163.com",
  pass: "a4hepfmfcktpte4h",
};

function isApk() {
  return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
}

function davCfg() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(DAV_KEY) || "null") || {};
  } catch (_) {
    stored = {};
  }
  return { ...DAV_DEFAULTS, ...stored };
}

function davSave(cfg) {
  localStorage.setItem(DAV_KEY, JSON.stringify(cfg || {}));
}

function davEnabled() {
  const cfg = davCfg();
  return !!(isApk() && cfg && cfg.user && cfg.pass);
}
async function davFetch(path, options = {}) {
  const cfg = davCfg();
  if (!cfg) throw new Error("WebDAV 未配置");
  const base = cfg.url || "https://dav.jianguoyun.com/dav/";
  const headers = {
    Authorization: "Basic " + btoa(unescape(encodeURIComponent(cfg.user + ":" + cfg.pass))),
    ...(options.headers || {}),
  };
  return fetch(base + path, { ...options, headers });
}

async function davGetText(path) {
  const resp = await davFetch(path);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error("WebDAV GET " + resp.status);
  return resp.text();
}

async function davPutText(path, text) {
  const resp = await davFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: text,
  });
  if (!resp.ok) throw new Error("WebDAV PUT " + resp.status);
  return true;
}

async function davMove(src, dest) {
  const resp = await davFetch(src, {
    method: "MOVE",
    headers: { Destination: (davCfg().url || "https://dav.jianguoyun.com/dav/") + dest },
  });
  return resp.ok;
}

async function davList(path) {
  const resp = await davFetch(path + "/", { method: "PROPFIND", headers: { Depth: "1" } });
  if (resp.status === 404) return [];
  if (!resp.ok) throw new Error("WebDAV PROPFIND " + resp.status);
  const text = await resp.text();
  const hrefs = [...text.matchAll(/<(?:[a-zA-Z0-9]*:)?href>\s*([^<]+)\s*<\/(?:[a-zA-Z0-9]*:)?href>/g)].map(
    (m) => m[1]
  );
  const base = (davCfg().url || "https://dav.jianguoyun.com/dav/").replace(/^https?:\/\/[^/]+/, "").replace(/\/$/, "");
  const out = [];
  for (const href of hrefs) {
    let rel = href.startsWith(base) ? href.slice(base.length) : href;
    rel = decodeURIComponent(rel).replace(/^\//, "").replace(/\/$/, "");
    if (!rel || rel === path || !rel.startsWith(path.split("/")[0])) continue;
    const tail = rel.slice(path.length + 1);
    if (tail && !tail.includes("/")) out.push(rel);
  }
  return out;
}

window.DAV = { isApk, davCfg, davSave, davEnabled, davGetText, davPutText, davMove, davList };
