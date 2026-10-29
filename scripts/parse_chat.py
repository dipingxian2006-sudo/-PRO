#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_chat.py — SIPO Relationship Intelligence Engine · 数据解析层

把聊天记录（微信导出的 TXT / JSON / CSV）解析成可量化的行为指标 JSON，
供上层「人物画像引擎 / 心理模型层 / 策略层」消费。

设计原则：
  * 只产出「可观测行为指标」，不做心理结论。心理推论交给上层模型。
  * 纯标准库，零依赖，本地运行，不联网。
  * 解析异常时降级并记录到 meta.warnings，不静默丢数据。

用法：
  python parse_chat.py -i chat.txt -o metrics.json
  python parse_chat.py -i chat.json --self "小王" -r report.md
  python parse_chat.py -i chat.txt -g 180 --format txt
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 词典
# ----------------------------------------------------------------------------
POSITIVE = [
    "哈哈", "哈哈哈", "嘿嘿", "嘻嘻", "开心", "高兴", "喜欢", "好呀", "好啊", "可以呀",
    "谢谢", "感谢", "辛苦", "棒", "厉害", "期待", "想你", "爱你", "么么", "抱抱",
    "嗯嗯", "好的呀", "甜", "可爱", "温柔", "靠谱", "感动", "赞", "陪你",
    "一起", "记得", "关心", "照顾", "惊喜", "漂亮", "好看", "太好了", "真好",
]
NEGATIVE = [
    "生气", "难过", "难受", "讨厌", "累了", "好累", "算了", "不想", "不行",
    "失望", "委屈", "崩溃", "无语", "别管", "压力", "焦虑", "不舒服", "没意思",
    "疏远", "冷战", "吵架", "伤心", "孤独", "emo", "烦死", "烦躁", "烦",
]
# 低投入信号：只在「整条消息基本只由这些词构成」时才判定，避免误伤（如"好吃"）
LOW_EFFORT = [
    "嗯", "哦", "噢", "好", "好吧", "行", "随便", "都可以", "都行", "无所谓",
    "看你", "你定", "都好", "再说", "先这样", "在忙", "忙", "晚点", "回头",
    "哈哈", "嗯嗯", "好的", "收到",
]
HEDGE = ["可能", "也许", "再看看", "再说吧", "有空", "到时候", "尽量", "争取", "应该"]
QUESTION_WORDS = [
    "吗", "呢", "什么", "为什么", "怎么", "哪里", "谁", "多少", "是不是",
    "好不好", "行不行", "能不能", "要不要", "有没有", "?", "？",
]
MEDIA_TAGS = [
    "[图片]", "[照片]", "[语音]", "[视频]", "[动画表情]", "[表情]", "[文件]",
    "[链接]", "[名片]", "[聊天记录]", "[位置]", "[转账]", "[红包]",
    "[语音通话]", "[视频通话]",
]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+")


def count_hits(text: str, words: list[str]) -> int:
    """长词优先并消耗匹配，避免'吵架'被'吵'重复计数。"""
    t, n = text, 0
    for w in sorted(words, key=len, reverse=True):
        c = t.count(w)
        if c:
            n += c
            t = t.replace(w, " ")
    return n


def is_low_effort(text: str) -> bool:
    """整条消息是否属于低投入/敷衍回复。"""
    s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
    if not s or len(s) > 8:
        return False
    if s in LOW_EFFORT:
        return True
    tmp, changed = s, True
    while changed:
        changed = False
        for w in sorted(LOW_EFFORT, key=len, reverse=True):
            if w and w in tmp:
                tmp = tmp.replace(w, "")
                changed = True
    return tmp == ""


# ----------------------------------------------------------------------------
# 解析：TXT
# ----------------------------------------------------------------------------
RE_TIME = (
    r"(?P<y>\d{4})[-/年](?P<mo>\d{1,2})[-/月](?P<d>\d{1,2})日?[\sT]+"
    r"(?P<ampm>凌晨|上午|早上|中午|下午|晚上)?\s*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?"
)
P_TIME_FIRST = re.compile(r"^\s*\[?\s*" + RE_TIME + r"\s*\]?\s*[-—]?\s*(?P<tail>.*)$")
P_NAME_FIRST = re.compile(r"^\s*(?P<tail>.+?)\s+[-—]?\s*\[?\s*" + RE_TIME + r"\s*\]?\s*$")

AMPM_PM = ("下午", "晚上")


def build_dt(m: re.Match) -> datetime | None:
    try:
        h = int(m.group("h"))
        if m.group("ampm") in AMPM_PM and h < 12:
            h += 12
        if m.group("ampm") == "凌晨" and h == 12:
            h = 0
        return datetime(int(m.group("y")), int(m.group("mo")), int(m.group("d")),
                        h, int(m.group("mi")), int(m.group("s") or 0))
    except Exception:
        return None


def split_tail(tail: str):
    """把 '昵称: 内容' 或 '昵称' 拆成 (昵称, 内容)。"""
    tail = tail.strip()
    if not tail:
        return None
    idxs = [i for i, c in enumerate(tail) if c in ":："]
    if idxs:
        i = idxs[0]
        name, rest = tail[:i].strip(), tail[i + 1:].strip()
        if 0 < len(name) <= 40:
            return name, rest
    return (tail, "") if len(tail) <= 40 else None


def parse_txt(path: Path, warnings: list[str]) -> list[dict]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig":
                warnings.append(f"编码回退：以 {enc} 解码")
            break
        except Exception:
            continue
    if text is None:
        raise RuntimeError("无法解码文件（已尝试 utf-8 / gbk / utf-16）")

    msgs: list[dict] = []
    cur: dict | None = None
    unmatched = 0

    for line in text.splitlines():
        if not line.strip():
            continue
        hit = None
        for pat in (P_TIME_FIRST, P_NAME_FIRST):
            m = pat.match(line)
            if m:
                dt = build_dt(m)
                parsed = split_tail(m.group("tail"))
                if dt and parsed and parsed[0]:
                    hit = (dt, parsed[0], parsed[1])
                    break
        if hit:
            dt, name, rest = hit
            cur = {"time": dt, "sender": name, "text": rest}
            msgs.append(cur)
        elif cur is not None:
            cur["text"] = (cur["text"] + "\n" + line.strip()).strip()
        else:
            unmatched += 1

    if unmatched:
        warnings.append(f"{unmatched} 行未匹配到时间戳，已忽略")
    if not msgs:
        raise RuntimeError("TXT 中未解析出任何消息，请检查导出格式")
    msgs.sort(key=lambda m: m["time"])
    return msgs


# ----------------------------------------------------------------------------
# 解析：JSON / CSV
# ----------------------------------------------------------------------------
TIME_KEYS = ["time", "timestamp", "ts", "date", "datetime", "createTime", "create_time", "msgCreateTime",
             "时间", "日期", "发送时间", "消息时间", "时间datetime", "时间戳"]
SENDER_KEYS = ["sender", "from", "speaker", "name", "nickname", "talker", "user", "who", "role",
               "发送者", "发送人", "发言人", "昵称", "姓名", "对方", "说话人"]
TEXT_KEYS = ["text", "content", "msg", "message", "body", "strContent", "content_text",
             "内容", "消息", "消息内容", "正文", "聊天内容"]
TYPE_KEYS = ["type", "msgType", "msg_type", "kind", "类型", "消息类型", "消息种类"]

# 类型字段 → 占位文本（内容列为空时用，保证媒体消息也进入时间轴）
TYPE_PLACEHOLDER = {
    "图片": "[图片]", "照片": "[图片]", "image": "[图片]", "图片消息": "[图片]",
    "视频": "[视频]", "video": "[视频]", "小视频": "[视频]",
    "语音": "[语音]", "voice": "[语音]",
    "动画表情": "[动画表情]", "表情": "[动画表情]", "sticker": "[动画表情]",
    "文件": "[文件]", "file": "[文件]",
    "链接": "[链接]", "分享": "[链接]", "url": "[链接]",
    "名片": "[名片]", "位置": "[位置]", "转账": "[转账]", "红包": "[红包]",
    "聊天记录": "[聊天记录]", "合并转发": "[聊天记录]",
    "通话": "[语音通话]", "语音通话": "[语音通话]", "视频通话": "[视频通话]",
    "系统消息": "[系统消息]", "撤回": "[撤回]",
}


def _type_placeholder(d: dict):
    """内容为空时，用类型列推断媒体占位文本；推断不出返回 None。"""
    t = _pick(d, TYPE_KEYS)
    if not t:
        return None
    t = str(t).strip()
    return TYPE_PLACEHOLDER.get(t, f"[{t}]" if t and t != "普通文本" else None)


def _pick(d: dict, keys: list[str]):
    low = {k.lower(): k for k in d.keys()}
    for k in keys:
        if k in d:
            return d[k]
        if k.lower() in low:
            return d[low[k.lower()]]
    return None


def to_dt(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        if n > 1e12:
            n /= 1000.0
        if n > 1e9:
            return datetime.fromtimestamp(n)
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y年%m月%d日 %H:%M",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[\sT]*(\d{1,2}):(\d{2})", s)
    if m:
        try:
            return datetime(*[int(x) for x in m.groups()])
        except Exception:
            return None
    return None


def _find_records(obj):
    if isinstance(obj, list):
        return obj if obj and isinstance(obj[0], dict) else None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_records(v)
            if r:
                return r
    return None


def _rows_to_msgs(rows, warnings, label):
    msgs, bad = [], 0
    for r in rows:
        dt = to_dt(_pick(r, TIME_KEYS))
        sender = _pick(r, SENDER_KEYS)
        text = _pick(r, TEXT_KEYS)
        if text is None or not str(text).strip():
            text = _type_placeholder(r) or ""
        if dt is None or not sender:
            bad += 1
            continue
        msgs.append({"time": dt, "sender": str(sender).strip(), "text": str(text or "").strip()})
    if bad:
        warnings.append(f"{bad} 条 {label} 记录缺时间或发送者，已跳过")
    if not msgs:
        raise RuntimeError(f"{label} 记录全部无法解析（缺时间/发送者字段）")
    msgs.sort(key=lambda m: m["time"])
    return msgs


def parse_json(path: Path, warnings: list[str]) -> list[dict]:
    recs = _find_records(json.loads(path.read_text(encoding="utf-8-sig")))
    if not recs:
        raise RuntimeError("JSON 中未找到消息数组")
    return _rows_to_msgs(recs, warnings, "JSON")


def parse_csv(path: Path, warnings: list[str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError("CSV 为空")
    return _rows_to_msgs(rows, warnings, "CSV")


def load_messages(path: Path, fmt: str, warnings: list[str]) -> list[dict]:
    if fmt == "auto":
        fmt = {".json": "json", ".csv": "csv"}.get(path.suffix.lower(), "txt")
    return {"txt": parse_txt, "json": parse_json, "csv": parse_csv}[fmt](path, warnings)


# ----------------------------------------------------------------------------
# 指标计算
# ----------------------------------------------------------------------------
def strip_media(text: str) -> str:
    t = text
    for tag in MEDIA_TAGS:
        t = t.replace(tag, "")
    return t.strip()


def build_sessions(msgs: list[dict], gap_minutes: int) -> list[list[dict]]:
    if not msgs:
        return []
    sessions, cur = [], [msgs[0]]
    for prev, nxt in zip(msgs, msgs[1:]):
        if (nxt["time"] - prev["time"]) > timedelta(minutes=gap_minutes):
            sessions.append(cur)
            cur = [nxt]
        else:
            cur.append(nxt)
    sessions.append(cur)
    return sessions


def pctl(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return float(s[min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))])


def compute(msgs: list[dict], gap_minutes: int, self_name: str | None) -> dict:
    counts = Counter(m["sender"] for m in msgs)
    names = [n for n, _ in counts.most_common()]
    if len(names) < 2:
        raise RuntimeError("只识别到 1 个发言者，无法做关系分析")
    main, others = names[:2], names[2:]
    A, B = main

    sessions = build_sessions(msgs, gap_minutes)
    t0, t1 = msgs[0]["time"], msgs[-1]["time"]
    span_days = max(1.0, (t1 - t0).total_seconds() / 86400.0)
    gap_sec = gap_minutes * 60

    per: dict[str, dict] = {}
    for n in main:
        mine = [m for m in msgs if m["sender"] == n]
        texts = [strip_media(m["text"]) for m in mine]
        lens = [len(t) for t in texts]
        joined = "\n".join(texts)
        per[n] = {
            "msg_count": len(mine),
            "char_total": sum(lens),
            "char_avg": round(statistics.mean(lens), 1) if lens else 0.0,
            "char_median": pctl([float(x) for x in lens], 0.5),
            "char_p90": pctl([float(x) for x in lens], 0.9),
            "media_count": sum(count_hits(m["text"], MEDIA_TAGS) for m in mine),
            "emoji_count": sum(len(EMOJI_RE.findall(m["text"])) for m in mine),
            "question_msgs": sum(1 for t in texts if any(w in t for w in QUESTION_WORDS)),
            "positive_hits": count_hits(joined, POSITIVE),
            "negative_hits": count_hits(joined, NEGATIVE),
            "hedge_hits": count_hits(joined, HEDGE),
            "low_effort_msgs": sum(1 for t in texts if is_low_effort(t)),
            "night_msgs": sum(1 for m in mine if m["time"].hour >= 23 or m["time"].hour < 5),
            "peak_hours": [h for h, _ in Counter(m["time"].hour for m in mine).most_common(3)],
            "initiated_sessions": 0,
            "closed_sessions": 0,
            "_latency": [],
            "max_streak": 0,
        }

    # 会话维度：发起者 / 收尾者 / 连发
    for sess in sessions:
        per[sess[0]["sender"]]["initiated_sessions"] += 1
        per[sess[-1]["sender"]]["closed_sessions"] += 1
        best = {A: 0, B: 0}
        run_by, run_len = None, 0
        for m in sess:
            if m["sender"] == run_by:
                run_len += 1
            else:
                run_by, run_len = m["sender"], 1
            if run_by in best:
                best[run_by] = max(best[run_by], run_len)
        for n in main:
            per[n]["max_streak"] = max(per[n]["max_streak"], best[n])

    # 回复延迟 / 回应率
    for i, m in enumerate(msgs):
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        if nxt is None:
            continue
        delta = (nxt["time"] - m["time"]).total_seconds()
        if nxt["sender"] != m["sender"] and 0 <= delta <= gap_sec:
            per[nxt["sender"]]["_latency"].append(delta)
            per[nxt["sender"]]["_responded"] = per[nxt["sender"]].get("_responded", 0) + 1

    # 统计每条消息是否属于「可被回应」的消息（非日志最后一条）
    eligible = {A: 0, B: 0}
    for i, m in enumerate(msgs):
        if i + 1 < len(msgs) and m["sender"] in eligible:
            eligible[m["sender"]] += 1

    for n in main:
        d, other = per[n], (B if n == A else A)
        lat = d.pop("_latency")
        resp = d.pop("_responded", 0)
        d["reply_latency_median_sec"] = round(statistics.median(lat), 1) if lat else None
        d["reply_latency_p75_sec"] = round(pctl(lat, 0.75), 1) if lat else None
        d["reply_latency_max_sec"] = round(max(lat), 1) if lat else None
        d["response_rate"] = round(resp / eligible[other], 3) if eligible[other] else None
        d["msgs_per_day"] = round(d["msg_count"] / span_days, 2)
        d["question_ratio"] = round(d["question_msgs"] / max(1, d["msg_count"]), 3)
        d["low_effort_ratio"] = round(d["low_effort_msgs"] / max(1, d["msg_count"]), 3)
        d["night_ratio"] = round(d["night_msgs"] / max(1, d["msg_count"]), 3)
        d["pos_neg_ratio"] = (round(d["positive_hits"] / d["negative_hits"], 2)
                              if d["negative_hits"] else None)

    def asym(k):
        a, b = float(per[A].get(k) or 0), float(per[B].get(k) or 0)
        return round(abs(a - b) / max(1e-9, a + b), 3)

    def share(k):
        a, b = float(per[A].get(k) or 0), float(per[B].get(k) or 0)
        return round(a / max(1e-9, a + b), 3)

    la, lb = per[A]["reply_latency_median_sec"], per[B]["reply_latency_median_sec"]
    latency_ratio = round(max(la, lb) / min(la, lb), 2) if (la and lb and min(la, lb) > 0) else None

    quarter = t0 + (t1 - t0) * 0.75
    recent = sum(1 for m in msgs if m["time"] >= quarter)
    early = len(msgs) - recent
    r_days = max(0.5, (t1 - quarter).total_seconds() / 86400.0)
    e_days = max(0.5, (quarter - t0).total_seconds() / 86400.0)
    momentum = round((recent / r_days) / max(0.01, early / e_days), 2)

    gaps = sorted(
        [((n["time"] - p["time"]).total_seconds(), p["time"], n["time"])
         for p, n in zip(msgs, msgs[1:])], reverse=True)[:3]

    relation = {
        "span_days": round(span_days, 1),
        "session_count": len(sessions),
        "session_gap_minutes": gap_minutes,
        "msg_total": len(msgs),
        "initiative_share": {A: share("initiated_sessions"), B: round(1 - share("initiated_sessions"), 3)},
        "closing_share": {A: share("closed_sessions"), B: round(1 - share("closed_sessions"), 3)},
        "initiative_skew": asym("initiated_sessions"),
        "volume_skew": asym("char_total"),
        "count_skew": asym("msg_count"),
        "depth_skew": asym("char_avg"),
        "question_skew": asym("question_msgs"),
        "latency_ratio": latency_ratio,
        "max_streak": {A: per[A]["max_streak"], B: per[B]["max_streak"]},
        "momentum_recent_vs_base": momentum,
        "top_silence_gaps": [
            {"hours": round(g[0] / 3600, 1),
             "from": g[1].strftime("%Y-%m-%d %H:%M"),
             "to": g[2].strftime("%Y-%m-%d %H:%M")} for g in gaps],
        "speakers_detected": names,
    }

    return {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "participants": [A, B],
            "other_speakers": others,
            "self": self_name or None,
            "other": (B if self_name == A else A) if self_name else None,
            "warnings": [],
        },
        "relation": relation,
        "per_person": per,
        "sessions_preview": [
            {"start": s[0]["time"].strftime("%Y-%m-%d %H:%M"), "by": s[0]["sender"],
             "count": len(s), "end": s[-1]["time"].strftime("%Y-%m-%d %H:%M"),
             "last_by": s[-1]["sender"]} for s in sessions[:50]
        ],
    }


# ----------------------------------------------------------------------------
# 信号检测：只描述行为，不做心理定性
# ----------------------------------------------------------------------------
def detect_signals(res: dict) -> list[dict]:
    rel, per = res["relation"], res["per_person"]
    A, B = res["meta"]["participants"]
    out: list[dict] = []

    def add(code, level, who, desc, ev):
        out.append({"code": code, "level": level, "who": who, "desc": desc, "evidence": ev})

    if rel["initiative_skew"] >= 0.3:
        top = A if per[A]["initiated_sessions"] > per[B]["initiated_sessions"] else B
        add("INITIATIVE_SKEW", "high" if rel["initiative_skew"] >= 0.5 else "mid", top,
            "开启对话高度集中在单方",
            f"{A}={per[A]['initiated_sessions']} / {B}={per[B]['initiated_sessions']}")
    if rel["latency_ratio"] and rel["latency_ratio"] >= 3:
        slow = A if (per[A]["reply_latency_median_sec"] or 0) > (per[B]["reply_latency_median_sec"] or 0) else B
        add("LATENCY_ASYMMETRY", "high" if rel["latency_ratio"] >= 6 else "mid", slow,
            "双方回复速度明显不对称",
            f"{A}={per[A]['reply_latency_median_sec']}s / {B}={per[B]['reply_latency_median_sec']}s")
    if rel["volume_skew"] >= 0.35:
        top = A if per[A]["char_total"] > per[B]["char_total"] else B
        add("VOLUME_SKEW", "mid", top, "文字投入量不对称",
            f"{A}={per[A]['char_total']}字 / {B}={per[B]['char_total']}字")
    for n in (A, B):
        d = per[n]
        if d["response_rate"] is not None and d["response_rate"] <= 0.4:
            add("LOW_RESPONSE", "high", n, "对方发来的消息常常没有接住",
                f"{n} 回应率 {d['response_rate']}")
        if d["max_streak"] >= 3:
            add("ONE_WAY_LONG", "mid", n, "存在连续单向输出（对方未接话）",
                f"{n} 最长连发 {d['max_streak']} 条")
        if d["low_effort_ratio"] >= 0.25:
            add("LOW_EFFORT", "mid", n, "低投入/敷衍回复占比偏高",
                f"{n} 占比 {d['low_effort_ratio']}（{d['low_effort_msgs']} 条）")
        if d["pos_neg_ratio"] is not None and d["pos_neg_ratio"] < 1:
            add("NEGATIVE_TILT", "high", n, "负向表达多于正向", f"{n} 正负比 {d['pos_neg_ratio']}")
        if d["night_ratio"] >= 0.3:
            add("NIGHT_HEAVY", "low", n, "高频深夜交流", f"{n} 深夜占比 {d['night_ratio']}")
    cs = rel["closing_share"]
    hi = max(cs, key=cs.get)
    if cs[hi] >= 0.7:
        add("CLOSING_SKEW", "mid", hi, "对话收尾高度集中在单方（另一方常不接最后一句）",
            f"收尾占比 {cs}")
    if rel["momentum_recent_vs_base"] <= 0.6:
        add("MOMENTUM_DROP", "high", "双方", "近期互动密度明显下滑",
            f"近 1/4 时间窗日均为基线的 {rel['momentum_recent_vs_base']} 倍")
    elif rel["momentum_recent_vs_base"] >= 1.5:
        add("MOMENTUM_UP", "low", "双方", "近期互动密度上升", f"倍数 {rel['momentum_recent_vs_base']}")
    return out


# ----------------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------------
def to_markdown(res: dict, signals: list[dict]) -> str:
    rel, per = res["relation"], res["per_person"]
    A, B = res["meta"]["participants"]
    L = ["# 聊天数据解析报告（行为指标层）\n"]
    L.append(f"- 时间跨度：**{rel['span_days']} 天** ｜ 消息数：**{rel['msg_total']}** ｜ "
             f"会话段：**{rel['session_count']}**（间隔 {rel['session_gap_minutes']} 分钟切分）")
    L.append(f"- 参与方：**{A}** / **{B}**")
    if res["meta"].get("self"):
        L.append(f"- 视角：用户本人 = {res['meta']['self']}，分析对象 = {res['meta']['other']}")
    L.append("")

    rows = [
        ("msg_count", " 条"), ("char_total", " 字"), ("char_avg", " 字/条"),
        ("msgs_per_day", " 条/天"), ("initiated_sessions", " 段发起"),
        ("closed_sessions", " 段收尾"), ("response_rate", ""),
        ("reply_latency_median_sec", " s"), ("question_ratio", ""),
        ("low_effort_ratio", ""), ("low_effort_msgs", " 条"),
        ("positive_hits", ""), ("negative_hits", ""),
        ("media_count", ""), ("emoji_count", ""),
        ("night_ratio", ""), ("max_streak", " 条"),
    ]
    L.append("## 逐项对照\n")
    L.append(f"| 指标 | {A} | {B} |")
    L.append("|---|---|---|")
    for k, u in rows:
        va, vb = per[A].get(k), per[B].get(k)
        L.append(f"| {k} | {f'{va}{u}' if va is not None else '—'} | {f'{vb}{u}' if vb is not None else '—'} |")

    L.append("\n## 关系级指标\n")
    L.append(f"- 主动发起占比：{rel['initiative_share']}")
    L.append(f"- 对话收尾占比：{rel['closing_share']}")
    L.append(f"- 投入偏斜（字数/条数/句均深度）：{rel['volume_skew']} / {rel['count_skew']} / {rel['depth_skew']}")
    L.append(f"- 提问偏斜：{rel['question_skew']} ｜ 回复速度比：{rel['latency_ratio']}")
    L.append(f"- 最长连发：{rel['max_streak']}")
    L.append(f"- 近期热度（近 1/4 时间窗 vs 基线）：{rel['momentum_recent_vs_base']}")
    L.append(f"- 最长沉默窗口：{rel['top_silence_gaps'][:3]}")

    L.append("\n## 检测到的行为信号\n")
    if signals:
        L.append("| 等级 | 信号 | 指向 | 说明 | 证据 |")
        L.append("|---|---|---|---|---|")
        for s in signals:
            L.append(f"| {s['level']} | `{s['code']}` | {s['who']} | {s['desc']} | {s['evidence']} |")
    else:
        L.append("无明显异常信号。")

    if res["meta"].get("warnings"):
        L.append("\n## 解析告警\n")
        for w in res["meta"]["warnings"]:
            L.append(f"- {w}")
    L.append("\n> 本报告为**数据解析层**输出，只呈现可观测行为指标；"
              "心理、性格、类型解读由上层模型完成，见主报告。")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="聊天记录 -> 行为指标 JSON")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", default=None, help="指标 JSON 输出路径")
    ap.add_argument("--report", "-r", default=None, help="Markdown 报告输出路径")
    ap.add_argument("--format", "-f", default="auto", choices=["auto", "txt", "json", "csv"])
    ap.add_argument("--gap", "-g", type=int, default=120, help="会话切分间隔（分钟），默认 120")
    ap.add_argument("--self", default=None, help="指定哪一方是用户本人")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"[ERROR] 文件不存在: {path}", file=sys.stderr)
        sys.exit(2)

    warnings: list[str] = []
    try:
        msgs = load_messages(path, args.format, warnings)
    except Exception as e:
        print(f"[ERROR] 解析失败: {e}", file=sys.stderr)
        sys.exit(3)

    res = compute(msgs, args.gap, args.self)
    res["meta"]["warnings"] = warnings
    res["signals"] = detect_signals(res)

    if args.output:
        Path(args.output).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 指标已写入 {args.output}")
    md = to_markdown(res, res["signals"])
    if args.report:
        Path(args.report).write_text(md, encoding="utf-8")
        print(f"[OK] 报告已写入 {args.report}")
    if not args.quiet and not args.report:
        print(md)


if __name__ == "__main__":
    main()
