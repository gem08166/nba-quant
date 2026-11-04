"""坚果云 WebDAV 最小客户端（httpx）。

只用到 PUT / GET / MKCOL / PROPFIND / MOVE / DELETE 六个动作，够台账同步用。
坚果云不返回 CORS 头，所以这层只能跑在服务端（本机 API/watch），浏览器直连必挂。

路径约定（相对 WEBDAV_URL，默认 https://dav.jianguoyun.com/dav/）：
    nbaq/bets.json        合并后的全量台账（PC 每轮推送，手机可导入）
    nbaq/inbox/*.json     手机导出的台账片段，PC 拉取合并后移入 done/
"""
from __future__ import annotations

import re

import httpx

from ..config import WEBDAV_PASS, WEBDAV_URL, WEBDAV_USER

FOLDER = "nbaq"
LEDGER = f"{FOLDER}/bets.json"
INBOX = f"{FOLDER}/inbox"
DONE = f"{INBOX}/done"


def enabled() -> bool:
    return bool(WEBDAV_USER and WEBDAV_PASS)


def _client() -> httpx.Client:
    return httpx.Client(
        auth=(WEBDAV_USER, WEBDAV_PASS),
        headers={"User-Agent": "nba-quant-sync/0.1"},
        timeout=30.0,
        follow_redirects=True,
    )


def _url(path: str) -> str:
    base = WEBDAV_URL if WEBDAV_URL.endswith("/") else WEBDAV_URL + "/"
    return base + path


def _mkcol_ok(status: int) -> bool:
    return status in (201, 301, 405)  # 405 = 已存在


def ensure_folders() -> bool:
    """创建 nbaq/、nbaq/inbox/、nbaq/inbox/done/（已存在不算错）。"""
    if not enabled():
        return False
    ok = True
    with _client() as client:
        for path in (FOLDER, INBOX, DONE):
            try:
                resp = client.request("MKCOL", _url(path + "/"))
                if resp.status_code not in (201, 405) and resp.status_code >= 400:
                    # 坚果云对已存在目录也可能返回别的码；只有 5xx 才算真失败
                    if resp.status_code >= 500:
                        ok = False
            except httpx.HTTPError:
                ok = False
    return ok


def put_text(path: str, text: str) -> bool:
    if not enabled():
        return False
    with _client() as client:
        resp = client.put(_url(path), content=text.encode("utf-8"))
        return resp.status_code in (200, 201, 204)


def get_text(path: str) -> str | None:
    """返回文件内容；不存在（404）返回 None，其他错误抛异常。"""
    if not enabled():
        return None
    with _client() as client:
        resp = client.get(_url(path))
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text


def list_files(path: str) -> list[str]:
    """PROPFIND Depth:1，返回该目录下的文件相对路径（不含目录本身）。"""
    if not enabled():
        return []
    with _client() as client:
        resp = client.request(
            "PROPFIND",
            _url(path + "/"),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        if resp.status_code in (404,):
            return []
        resp.raise_for_status()
    hrefs = re.findall(r"<[a-zA-Z0-9]*:?href>\s*([^<]+)\s*</[a-zA-Z0-9]*:?href>", resp.text)
    prefix = re.sub(r"^https?://[^/]+", "", WEBDAV_URL)
    files: list[str] = []
    for href in hrefs:
        href = href.strip()
        rel = href[len(prefix):].lstrip("/") if href.startswith(prefix) else href.lstrip("/")
        rel = rel.rstrip("/")
        if not rel or rel == path.rstrip("/") or rel.endswith("/"):
            continue
        # 只要是目录下的直接子项且不是目录本身
        if "/" not in rel[len(path.rstrip("/")) + 1:] and rel != path.rstrip("/"):
            files.append(rel)
    return files


def move(src: str, dest: str) -> bool:
    if not enabled():
        return False
    with _client() as client:
        resp = client.request("MOVE", _url(src), headers={"Destination": _url(dest)})
        return resp.status_code in (200, 201, 204)
