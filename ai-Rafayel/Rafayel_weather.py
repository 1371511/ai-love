# -*- coding: utf-8 -*-
"""
🌤 温度感知（2026-09-24 加）—— 参考城市：**中国上海**。

她定的三条口径：
  ① 温度**只在心里知道，她问才说**（跟时间同一个道理：她刚提过「时间提得太频繁」）
  ② 例外：**突变时关怀一句** —— 降温 / 高温 / 严寒 / 下雨雪，一天最多一次，
     说成关心（「多穿点」）别像播天气
  ③ **不提城市名** —— 她是深圳的，他说「上海今天 23 度」很出戏；
     上海只是**参考值**，用来撑起冷暖的质感

数据源：open-meteo（免费、免 key、按经纬度）。
  · 一次请求同时拿：当前温度 / 体感 / 天气码 + 昨天和今天的最高最低温
    ⇒ **降温判定不用自己存历史**（换机器、重启都不丢）
  · 测过 wttr.in 也能用，但一次 39KB，open-meteo 只要 487 字节

⭐⭐ 两条铁律：
  ① **联网失败 = 静默降级**：拿不到就当没这项功能，绝不能把对话搞崩
     （人设卡读不到才报错；这类锦上添花的一律降级）
  ② **绝不因为等天气拖慢回复**：超时 5 秒，且失败后歇 10 分钟再试

分层：本模块只依赖 config，是**最底层**（跟 config 平级），谁都能 import，绝不反向依赖。
"""
import json
import os
import time

from Rafayel_config import (
    MEMORY_DIR, WEATHER, WEATHER_CITY, WEATHER_COLD_MIN, WEATHER_DROP_DELTA,
    WEATHER_FAIL_TTL_SECONDS, WEATHER_HOT_MAX, WEATHER_LAT, WEATHER_LON,
    WEATHER_TIMEOUT, WEATHER_TTL_SECONDS,
)

def _nudge_path():
    """
    「今天关怀过了」记在**全局**文件里（温度是全局的，不按用户分）。

    ⚠ 为什么写成**函数**而不是模块级常量：MEMORY_DIR 一旦被改（测试会改），
      常量算出来的路径就对不上了（真踩到：测试里改了 MEMORY_DIR，文件却仍写去真实目录）。
      跟 `Rafayel_greet._record_path` 同一个写法。
    ⚠ 文件名不带 uid ⇒ 打招呼 / 发说说 / 节日那几个扫 MEMORY_DIR 的循环都会跳过它
      （它们只认纯数字 uid）。
    """
    return os.path.join(MEMORY_DIR, "weather.json")

# 进程内缓存：{"at": 时间戳, "data": 数据 or None}
#   ⚠ 失败的也会缓存（`data=None`），只是 TTL 更短 —— 否则每轮都去卡 5 秒超时。
_CACHE = {"at": 0.0, "data": None, "ok": False}

# WMO 天气码 → 中文。挑的是他会用得上的词，不是气象台口径。
WMO_CN = {
    0: "晴", 1: "大部晴朗", 2: "多云间晴", 3: "阴",
    45: "有雾", 48: "有雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}

# 算「有降水」的天气码（小雨雪也算 —— 他提醒带伞就是这种天）
WET_CODES = set(range(51, 68)) | set(range(71, 78)) | set(range(80, 87)) | {95, 96, 99}


def _url():
    return ("https://api.open-meteo.com/v1/forecast"
            "?latitude=%s&longitude=%s"
            "&current=temperature_2m,apparent_temperature,weather_code"
            "&daily=temperature_2m_max,temperature_2m_min,weather_code"
            "&past_days=1&forecast_days=1&timezone=Asia/Shanghai"
            % (WEATHER_LAT, WEATHER_LON))


def _fetch():
    """真去请求一次。返回解析好的 dict；失败返回 None（绝不抛出去）。"""
    try:
        import requests
    except Exception:
        return None
    try:
        s = requests.Session()
        # ⚠ 本机有 HTTP_PROXY 时 requests 会走代理（记忆里的坑：连本地也走、表现全 404）。
        #   这里两个都测通过，但服务器上大概率没代理 ⇒ 直连更稳。
        s.trust_env = False
        r = s.get(_url(), timeout=WEATHER_TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:
        return None

    cur = d.get("current") or {}
    daily = d.get("daily") or {}
    if cur.get("temperature_2m") is None:
        return None

    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    # daily 数组是 [昨天, 今天]（past_days=1 + forecast_days=1）
    today_max = tmax[-1] if tmax else None
    today_min = tmin[-1] if tmin else None
    yday_max = tmax[-2] if len(tmax) >= 2 else None

    drop = None
    if yday_max is not None and today_max is not None:
        diff = round(float(yday_max) - float(today_max), 1)
        if diff >= WEATHER_DROP_DELTA:
            drop = diff

    code = cur.get("weather_code")
    return {
        "temp": cur.get("temperature_2m"),
        "apparent": cur.get("apparent_temperature"),
        "code": code,
        "desc": WMO_CN.get(code, ""),
        "tmax": today_max,
        "tmin": today_min,
        "drop": drop,
        "wet": (code in WET_CODES),
    }


def current(force=False):
    """
    当前天气。拿不到返回 None（调用方当没这项功能）。

    ⚠ 缓存：成功 TTL 1 小时、失败 TTL 10 分钟。
       ⇒ 正常情况下一小时最多一次请求；网络挂了也不会每轮都卡超时。
    """
    if not WEATHER:
        return None
    now = time.time()
    ttl = WEATHER_TTL_SECONDS if _CACHE["ok"] else WEATHER_FAIL_TTL_SECONDS
    if not force and _CACHE["at"] and (now - _CACHE["at"]) < ttl:
        return _CACHE["data"]

    data = _fetch()
    _CACHE["at"] = now
    _CACHE["data"] = data
    _CACHE["ok"] = data is not None
    return data


def reset():
    """测试用：清缓存，强制下次重新请求。"""
    _CACHE["at"] = 0.0
    _CACHE["data"] = None
    _CACHE["ok"] = False


def _load_nudge():
    path = _nudge_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_nudge(d):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = _nudge_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _nudge_text(w):
    """
    今天有没有值得关怀的「突变」。返回给**他看的指令**（不是要说出口的话）；没有返回 ""。

    ⚠ 判据都用 open-meteo 给的**今天/昨天最高最低温**，不自己存历史 ⇒ 换机器也不丢。
    """
    if not w:
        return ""
    if w.get("drop"):
        return "今天比昨天降了 %s 度，可以顺口让她加件衣服" % _fmt(w["drop"])
    if w.get("tmax") is not None and float(w["tmax"]) >= WEATHER_HOT_MAX:
        return "今天最高 %s 度，可以顺口让她别在外面晒着" % _fmt(w["tmax"])
    if w.get("tmin") is not None and float(w["tmin"]) <= WEATHER_COLD_MIN:
        return "今天最低只有 %s 度，可以顺口让她穿厚点" % _fmt(w["tmin"])
    if w.get("wet"):
        return "今天%s，可以顺口问她带伞没" % (w.get("desc") or "有雨")
    return ""


def _fmt(v):
    """温度打印：整数就不带小数（「降了 9 度」比「降了 9.0 度」像话）。"""
    try:
        f = float(v)
    except Exception:
        return str(v)
    return str(int(round(f))) if abs(f - round(f)) < 0.05 else ("%.1f" % f)


def nudge():
    """
    今天的突变关怀 —— **一天最多一次**，取过就当用掉了。

    ⚠⭐ 为什么是「取了就算用过」而不是「等他真说了再记」：
       这段话是**混在 prompt 里的指令**，不像打招呼/发说说那样有独立的发送动作可以回调。
       拿「他到底说没说」去判断，得事后扫他的回复，成本高也不准。
       ⇒ 取舍：偶尔她没听到那句关心（模型没照做），也**好过每轮都提醒她穿衣**。
    """
    if not WEATHER:
        return ""
    w = current()
    text = _nudge_text(w)
    if not text:
        return ""
    today = time.strftime("%Y-%m-%d")
    rec = _load_nudge()
    if rec.get("date") == today:
        return ""
    rec["date"] = today
    rec["what"] = text
    _save_nudge(rec)
    return text


def weather_line():
    """
    拼 prompt 里那句天气描述。**不提城市名**（她定的 ③）。

    例：「外面 23 度，体感 26 度，大部晴朗。」
    """
    w = current()
    if not w or w.get("temp") is None:
        return ""
    parts = ["外面 %s 度" % _fmt(w["temp"])]
    if w.get("apparent") is not None:
        parts.append("体感 %s 度" % _fmt(w["apparent"]))
    if w.get("desc"):
        parts.append(w["desc"])
    return "，".join(parts) + "。"


def debug_text():
    """启动自检 / 排查看的：带城市名（只在日志里，不进 prompt）。"""
    w = current()
    if not w:
        return "%s：拿不到天气（功能静默关闭）" % WEATHER_CITY
    return "%s %s 度（体感 %s）%s，今天 %s~%s 度%s" % (
        WEATHER_CITY, _fmt(w.get("temp")), _fmt(w.get("apparent")),
        w.get("desc") or "", _fmt(w.get("tmin")), _fmt(w.get("tmax")),
        "，比昨天降 %s 度" % _fmt(w["drop"]) if w.get("drop") else "")
