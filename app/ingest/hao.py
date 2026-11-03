from __future__ import annotations

"""好体育（39374.app）第二盘口源客户端。

逆向自 2026-11-03 的公开网页（个人研究用途，只读赔率，不注册不登录）：
- `sport/tournament/info?sid=2`（篮球）：返回所有篮球联赛，每个联赛内嵌 matches 列表
- `sport/match/simple?sid=2&iidList=...&inplay=false`：单场盘口
- 鉴权：`x-checksum = base64("<checksum-hex>|<镜像域名>|<uuid>|<毫秒>|")`

多镜像（2026-11-03 实测）：39374.app（api: 0062zyayk-nwapi-wap-dcdn.dovpkzr.com）与
333ty331.app（api: gle-nwapi.irkcx.com）是同一白标产品的两个部署实例，端点路径、
数据完全一致，仅 checksum-hex 按域名绑定。`_get` 会跨镜像自动故障转移，并从
镜像首页 HTML 自动发现新的 checksum（防轮换）。

风险与对策（务必了解）：
- 镜像域名 / checksum 机制随时可能变化 —— 所有失败都应被上层吞掉降级，绝不阻塞主流程
- 亚盘水位是港式赔率（0.89 = 净回报 0.89），欧赔 = 1 + 水位
- 域名是数字镜像，换域名时只需改 BASE

盘口结构（market 字段）：
  ml    {h, a}                     独赢（欧赔，含商家水位）
  1x2   {h, d, a}                  含平局欧赔（篮球基本用不上）
  ah    [{k: "-1.5", h, a}, ...]   亚洲让分（多档，k 为主队让球数，港式水位）
  ou    [{k: "218.5", ov, ud}, ...] 全场大小分（多档）
  ah_1st / h-ou / a-ou             半场让分 / 主客单队总分
"""

import base64
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx

from ..config import REQUEST_SLEEP, REQUEST_TIMEOUT

BASE = "https://0062zyayk-nwapi-wap-dcdn.dovpkzr.com"
CHECKSUM_HEX = "33069054802374b169bef34b3c4520e4ab9675165286e92cdbf06ab46920a7a5"
UUID = "2f9bb3caad4e79bb0ad93de3d5b17f06"

# 同一白标产品（tiger-fluid）的多个镜像域名。数据完全一致（2026-11-03 实测：
# 39374.app 与 333ty331.app 的 NBA 盘口逐字节相同），但 checksum-hex 与 API
# 主机按部署实例绑定。镜像随时会换：优先自动从页面 HTML 发现 checksum，
# 失败再用静态配置；逐个尝试直到成功。
MIRRORS = [
    {
        "web": "https://39374.app",
        "api": "https://0062zyayk-nwapi-wap-dcdn.dovpkzr.com",
        "checksum": "33069054802374b169bef34b3c4520e4ab9675165286e92cdbf06ab46920a7a5",
    },
    {
        "web": "https://333ty331.app",
        "api": "https://gle-nwapi.irkcx.com",
        "checksum": "ae5a7b090bd7c8adefd36c3efe7013933daa929b1eb562d15388f341341515cb",
    },
]

SID_BASKETBALL = 2
NBA_CID = 12
NBA_TID = 7067

_last_request_at = 0.0
_active_mirror: dict | None = None


class HaoError(RuntimeError):
    pass


def _discover_checksum(client: httpx.Client, web: str) -> str | None:
    """从镜像首页 HTML 里自动发现该域名绑定的 checksum hex。"""
    try:
        resp = client.get(
            f"{web}/sportEvents",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"},
            timeout=REQUEST_TIMEOUT,
        )
        match = re.search(r'"x-checksum":"([0-9a-f]{32,80})"', resp.text)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def _headers(mirror: dict) -> dict:
    global _last_request_at
    gap = time.time() - _last_request_at
    if gap < REQUEST_SLEEP:
        time.sleep(REQUEST_SLEEP - gap)
    _last_request_at = time.time()
    ms = int(time.time() * 1000)
    web_host = mirror["web"].split("//", 1)[1]
    checksum = base64.b64encode(f"{mirror['checksum']}|{web_host}|{UUID}|{ms}|".encode()).decode()
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": mirror["web"],
        "Referer": f"{mirror['web']}/",
        "apptype": "2",
        "x-checksum": checksum,
        "x-uuid": UUID,
        "currency": "CNY",
        "time-zone": "GMT+08:00",
        "device": "mobile",
    }


def _get(path: str, params: dict) -> dict:
    """跨镜像请求：优先复用上次成功的镜像，失败则逐个尝试（含 checksum 自动发现）。"""
    global _active_mirror, _last_request_at
    order = ([_active_mirror] if _active_mirror else []) + [m for m in MIRRORS if m is not _active_mirror]
    last_error: Exception | None = None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for mirror in order:
                try:
                    gap = time.time() - _last_request_at
                    if gap < REQUEST_SLEEP:
                        time.sleep(REQUEST_SLEEP - gap)
                    _last_request_at = time.time()
                    resp = client.get(f"{mirror['api']}{path}", params=params, headers=_headers(mirror))
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("code") != 0:
                        raise HaoError(f"hao api code={data.get('code')} msg={data.get('msg')}")
                    _active_mirror = mirror
                    return data.get("data") or {}
                except Exception as exc:  # noqa: BLE001 - try next mirror
                    last_error = exc
                    # checksum 可能随时间轮换：刷新该镜像的静态值再试下一次
                    discovered = _discover_checksum(client, mirror["web"])
                    if discovered:
                        mirror["checksum"] = discovered
        raise HaoError(f"all hao mirrors failed: {last_error}")
    except HaoError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HaoError(f"hao request failed: {exc}") from exc


def hk_to_decimal(hk: str | float | None) -> float | None:
    """港式水位 -> 欧赔。0.89 -> 1.89；已是小数赔率（>=1.3）则原样返回。"""
    try:
        value = float(hk)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(value + 1.0, 3) if value < 1.3 else round(value, 3)


def fetch_nba_matches() -> list[dict]:
    """NBA 全部近期比赛（含已结束/进行中/未开赛，带基本盘口元数据）。"""
    data = _get(
        "/product/business/sport/tournament/info",
        params={"sid": SID_BASKETBALL, "sort": "tournament", "inplay": "false", "language": "zh-cn"},
    )
    for tournament in data.get("tournaments") or []:
        if tournament.get("cid") == NBA_CID and tournament.get("tid") == NBA_TID:
            return tournament.get("matches") or []
    return []


def fetch_match_odds(iid: int | str) -> dict | None:
    """单场盘口（market 字段），拿不到返回 None。"""
    try:
        data = _get(
            "/product/business/sport/match/simple",
            params={"sid": SID_BASKETBALL, "iidList": str(iid), "inplay": "false", "language": "zh-cn"},
        )
        matches = data.get("matches") or []
        return matches[0] if matches else None
    except Exception:  # noqa: BLE001 - 第二源必须可失败
        return None


def parse_market(market: dict | None) -> dict:
    """把 market 字段转成研究友好的结构（全部换算成欧赔小数）。"""
    if not market:
        return {}
    result: dict[str, Any] = {}

    ml = market.get("ml") or {}
    result["ml"] = {
        "home": hk_to_decimal(ml.get("h")),
        "away": hk_to_decimal(ml.get("a")),
    }

    ah = []
    for row in market.get("ah") or []:
        ah.append(
            {
                "line": float(row["k"]) if row.get("k") is not None else None,
                "home_price": hk_to_decimal(row.get("h")),
                "away_price": hk_to_decimal(row.get("a")),
            }
        )
    result["ah"] = ah

    ou = []
    for row in market.get("ou") or []:
        ou.append(
            {
                "line": float(row["k"]) if row.get("k") is not None else None,
                "over_price": hk_to_decimal(row.get("ov")),
                "under_price": hk_to_decimal(row.get("ud")),
            }
        )
    result["ou"] = ou
    return result


def match_summary(match: dict) -> dict:
    """从 tournament/match 节点提取：iid、主客队名、开球时间（UTC 秒）、状态。"""
    return {
        "iid": match.get("iid"),
        "name": match.get("name") or "",
        "home": (match.get("home") or {}).get("name") or "",
        "away": (match.get("away") or {}).get("name") or "",
        "kickoff": match.get("kickoffTime") or match.get("kickoff"),
        "kickoff_dt": match.get("kickoffDT"),
        "state": match.get("state") or "",
        "inplay": bool(match.get("inplay")),
    }


def store_nba_odds(conn, slate_date: str) -> int:
    """把好体育当日 NBA 主盘口写入 odds_snapshots（book='hao-tiyu'）。

    队名匹配：好体育与 ESPN 均使用英文全名（大小写不敏感）；
    日期口径：kickoff(UTC 秒) - 5h = 美国东部比赛日。失败抛 HaoError/返回 0。
    """
    import sqlite3

    from ..config import REQUEST_SLEEP  # noqa: F401 - _headers 已限速
    from . import service as espn_service

    games = conn.execute(
        """
        SELECT g.id, h.name AS home_name, a.name AS away_name
        FROM games g JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
        WHERE g.slate_date=?
          AND g.status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
        """,
        (slate_date,),
    ).fetchall()

    inserted = 0
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for match in fetch_nba_matches():
            if match.get("state") not in ("", "not_start"):
                continue
            summary = match_summary(match)
            et_date = datetime.utcfromtimestamp((summary["kickoff"] or 0) - 5 * 3600).strftime("%Y-%m-%d")
            if et_date != slate_date:
                continue
            game = next(
                (
                    g
                    for g in games
                    if g["home_name"].lower() in summary["home"].lower()
                    and g["away_name"].lower() in summary["away"].lower()
                ),
                None,
            )
            if game is None:
                continue
            detail = fetch_match_odds(summary["iid"])
            if not detail:
                continue
            market = parse_market(detail.get("market"))
            rows: list[dict] = []
            ah = (market.get("ah") or [{}])[0]
            if ah.get("line") is not None:
                rows.append({"book": "hao-tiyu", "market": "spread", "side": "home", "line": ah["line"], "price": ah["home_price"], "source": "hao"})
                rows.append({"book": "hao-tiyu", "market": "spread", "side": "away", "line": -ah["line"], "price": ah["away_price"], "source": "hao"})
            ou = (market.get("ou") or [{}])[0]
            if ou.get("line") is not None:
                rows.append({"book": "hao-tiyu", "market": "total", "side": "over", "line": ou["line"], "price": ou["over_price"], "source": "hao"})
                rows.append({"book": "hao-tiyu", "market": "total", "side": "under", "line": ou["line"], "price": ou["under_price"], "source": "hao"})
            ml = market.get("ml") or {}
            if ml.get("home"):
                rows.append({"book": "hao-tiyu", "market": "moneyline", "side": "home", "line": None, "price": ml["home"], "source": "hao"})
            if ml.get("away"):
                rows.append({"book": "hao-tiyu", "market": "moneyline", "side": "away", "line": None, "price": ml["away"], "source": "hao"})
            inserted += espn_service.insert_odds(conn, game["id"], rows)
    return inserted
