# -*- coding: utf-8 -*-
"""
统一分析入口（一次读取 · 多方法并行 · 共享逐句时间轴）

设计要点（为什么比分别跑四个脚本强）：
  1. **只解析一次**：聊天记录读一遍，得到唯一的逐句时间轴；四个方法共用它，
     不会出现"同一句话在两个方法里被标成两种东西"的矛盾。
  2. **每句都参与四个方法**：
     - 心态波段：逐句打标签 → 平滑 → 波段 → 升降点
     - VOL：事件锚点**自动取自波段升降点**（不再手写清单），窗内逐句复核
     - 差评/好评：逐句筛正负向表达，并且**算出说完之后她的 trend 变化**（效果值）
     - 异动：维度漂移 + 质性引文定位到具体日期与所在波段
  3. **交叉验证自动化**：波段方向 vs VOL 方向 vs 异动方向，冲突自动列出。
  4. **统一输出**：analysis_all.json（含四个方法的完整结果 + 交叉验证 + 复盘骨架数据）。

用法（两种 CLI 写法都支持）：
  A) 位置参数（原版）：python analyze_all.py <chat.txt> --self 清峰 --other 柒月 [--window 5] [--events e.json] [--outdir DIR]
  B) 具名参数（schema 2.0）：python analyze_all.py --input <chat.txt> --self 清峰 [--other 柒月] --outdir DIR [--time-unreliable]

安全层（来自 schema 2.0 的 chat_engine，作为解析前处理接入，不改动分析口径）：
  * 输入边界校验：后缀 / 32MiB / 100000 条 / 非法 Unicode / UTF-16
  * 提示注入防护：聊天正文中的命令、角色声明、"忽略规则"一律视为数据，不改变工作流
  * 证据可追溯：每条消息带 id + 源文件 SHA256 + 行号/记录号；meta.parse 告警不静默吞掉
  * 失败不伪装：写盘原子替换，失败标记 FAILED.txt
  --events 可选：手写事件清单 JSON（[{"date":"2026-09-15","desc":"..."}]），
                 不给则自动从波段升降点取候选事件。
  --outdir 可选：产物输出目录；**默认写到输入文件所在目录**（避免从 skill 目录运行时
                 把 band.json 等产物污染进 skill 本身）。
输出：analysis_all.json（统一结果）
      同时兼容写出 band.json / vol_events.json / drift.json / pb.json / drift_qual.json（与单跑脚本同结构），
      供案例级报告生成器（如 build_retro.py）直接消费，避免两条数据链路分裂。
"""
import sys, json, re, statistics, os
from datetime import datetime, timedelta
from collections import defaultdict

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import drift_qual as DQ            # 质性维度异动（价值观/三观/性格/兴趣/谈话风格）
except Exception:
    DQ = None

# ★ 安全层（schema 2.0 的 chat_engine）：输入校验 + 溯源，仅作解析前处理
try:
    import chat_engine as CE
except Exception:
    CE = None

# ============ 词表（与 band_analysis.py / praise_blame_scan.py 保持一致）============
ENTHUSIASM = ["哈哈","哈哈哈","嘿嘿","嘻嘻","开心","高兴","喜欢","好呀","好啊","可以呀","期待","想你","爱你",
              "么么","抱抱","棒","厉害","可爱","甜","惊喜","漂亮","好看","太好了","真好","一起","陪你","记得"]
AGREE = ["好的","可以","行","ok","OK","嗯嗯","收到","都行","你定","听你的","没问题","好呢","好的呀"]
DEFENSE = ["不是","没有啊","哪有","怎么会","你别","我哪","才不是","开玩笑啦","你想多了","瞎说","才没有"]
RESIST = ["不行","不方便","算了","不用了","改天","再说吧","下次吧","不要","别了","我不了","不去","不行了"]
AVOID = ["嗯","哦","噢","好吧","随便","看你","都好","先这样","在忙","忙","晚点","回头","都可以"]
VULNERABLE = ["累","好累","难过","难受","委屈","崩溃","压力","焦虑","失眠","emo","不舒服","烦","疼","难受死了"]
PROBE_MARK = ["是不是","你觉得","你觉得呢","应该","要是","如果","会不会","真的吗","?","？","吗","呢"]
MEDIA = ["[图片]","[照片]","[视频]","[表情]","[动画表情]","[语音]"]

CRITICIZE = ["你怎么","你每次","你总是","你老是","你就这样","你这个人","你这样不行","你不对","你太","你别老"]
NEG_EVAL = ["不好","不行","不对","太差","不合适","不靠谱","离谱","过分","让人失望","没意思","无聊","烦人","讨厌"]
DEMAND = ["你必须","你给我","你说清楚","你发我","发我一张","发张","发一张","拍给我","你得","你到底"]
COMPLAIN_YOU = ["你又不","你都没","你从来","你每次都","你不觉得"]
PRAISE = ["好看","漂亮","厉害","棒","优秀","可爱","甜","温柔","体贴","懂事","喜欢你","爱你","想你",
          "真好","太好了","不错","很赞","点赞","佩服","感谢","谢谢你","辛苦了","有你在","开心"]
PRAISE_ROLE = ["你真懂事","你很懂事","你真乖","你很乖","你真好养","你真体贴","你很体贴"]
BLAME_SAFE = ["不好意思","还不错","你又不认识","你又不是"]

LINE_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>[^:：]+)[:：](?P<txt>.*)$")

LABEL_SCORE = {"enthusiasm": 2, "agreement": 1, "neutral": 0, "probe": 0,
               "vulnerable": -1, "defensive": -1, "avoidance": -1, "resistance": -2}
LABEL_CN = {"enthusiasm": "热忱", "agreement": "同意", "neutral": "中性", "probe": "试探",
            "vulnerable": "脆弱", "defensive": "防御", "avoidance": "回避", "resistance": "抗拒"}

# 以下词表与 drift_analysis.py / vol_analysis.py 严格一致（口径必须统一，否则两套数字打架）
POSITIVE = ["哈哈","哈哈哈","嘿嘿","嘻嘻","开心","高兴","喜欢","好呀","好啊","可以呀","谢谢","感谢",
            "辛苦","棒","厉害","期待","想你","爱你","么么","抱抱","嗯嗯","好的呀","甜","可爱","温柔",
            "靠谱","感动","赞","陪你","一起","记得","关心","照顾","惊喜","漂亮","好看","太好了","真好"]
NEGATIVE = ["生气","难过","难受","讨厌","累了","好累","算了","不想","不行","失望","委屈","崩溃","无语",
            "别管","压力","焦虑","不舒服","没意思","疏远","冷战","吵架","伤心","孤独","emo","烦死","烦躁","烦"]
HEDGE = ["可能","也许","再看看","再说吧","有空","到时候","尽量","争取","应该"]
QUESTION_WORDS = ["吗","呢","什么","为什么","怎么","哪里","谁","多少","是不是","好不好","行不行",
                  "能不能","要不要","有没有","?","？"]
LOW_EFFORT = ["嗯","哦","噢","好","好吧","行","随便","都可以","都行","无所谓","看你","你定","都好",
              "再说","先这样","在忙","忙","晚点","回头","哈哈","嗯嗯","好的","收到"]
POS_W, NEG_W, HEDGE_W = POSITIVE, NEGATIVE, HEDGE


def has(t, ws):
    return any(w in t for w in ws)


def count_hits(t, ws):
    return sum(t.count(w) for w in ws)


def parse(path, time_unreliable=False):
    # ★ 安全层前置校验（若 chat_engine 可用）：后缀/大小/条数/Unicode/UTF-16
    if CE is not None:
        try:
            CE.load_upload(path)          # 只做校验，失败抛 InputError
        except CE.InputError as e:
            raise SystemExit(f"输入未通过安全校验：{e}")
        except Exception:
            pass                          # 校验器自身异常不阻断主流程
    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        raw = m.group("ts").replace("-", "/")
        ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
        rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])
    # ★ 回退：本模块只认「单行带冒号」格式，若解析为 0 条（如双行式导出 / JSON / CSV），
    #   复用 parse_chat.py 的解析器（它支持「时间+姓名独占一行、内容另起一行」及 JSON/CSV），
    #   避免 Step 1 能跑而 Step 3.6 跑不出东西的口径分叉。
    if not rows:
        try:
            import parse_chat as PC
            from pathlib import Path
            warn = []
            msgs = PC.load_messages(Path(path), "auto", warn)
            def _clean(t):
                for tag in MEDIA:
                    t = t.replace(tag, "")
                return t.strip()
            rows = [{"ts": m["time"], "who": m["sender"], "txt": _clean(m.get("text", ""))}
                    for m in msgs if m.get("text")]
            rows.sort(key=lambda r: r["ts"])
            if warn:
                print("[parse 回退 parse_chat] " + "；".join(warn[:4]))
        except Exception as e:
            print(f"[parse 回退失败] {e}")
    return rows


# ---------- 方法①：心态波段（逐句）----------
def band_of(txt):
    t = txt.strip()
    if not t:
        return "neutral"
    if has(t, RESIST):
        return "resistance"
    if has(t, DEFENSE):
        return "defensive"
    if has(t, VULNERABLE):
        return "vulnerable"
    if has(t, ENTHUSIASM):
        return "enthusiasm"
    if has(t, AGREE):
        return "agreement"
    if any(m in t for m in MEDIA):
        return "enthusiasm"
    if has(t, PROBE_MARK):
        return "probe"
    if t in AVOID or (len(t) <= 2 and has(t, AVOID)):
        return "avoidance"
    return "neutral"


def run_band(rows, other, window=5):
    ann = []
    for i, r in enumerate(rows):
        if r["who"] != other:
            continue
        lb = band_of(r["txt"])
        ann.append({"idx": i, "time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                    "label": lb, "label_cn": LABEL_CN[lb], "score": LABEL_SCORE[lb],
                    "text": r["txt"][:60], "ts": r["ts"]})
    for i, a in enumerate(ann):
        win = ann[max(0, i - window + 1): i + 1]
        a["trend"] = round(sum(x["score"] for x in win) / len(win), 2)

    # 按日聚合 → 切波段
    per_day = defaultdict(list)
    for a in ann:
        per_day[a["time"][:10]].append(a["trend"])
    day_seq = [{"day": d, "trend": round(statistics.mean(v), 2), "n": len(v)}
               for d, v in sorted(per_day.items())]
    bands, cur = [], None
    for dd in day_seq:
        lvl = "up" if dd["trend"] >= 0.6 else ("down" if dd["trend"] <= -0.4 else "flat")
        if cur and cur["level"] == lvl:
            cur["end"] = dd["day"]; cur["n_msgs"] += dd["n"]; cur["days"].append(dd["trend"])
        else:
            if cur:
                bands.append(cur)
            cur = {"level": lvl, "start": dd["day"], "end": dd["day"],
                   "n_msgs": dd["n"], "days": [dd["trend"]]}
    if cur:
        bands.append(cur)
    for i, b in enumerate(bands):
        b["id"] = f"B{i+1}"
        b["trend_avg"] = round(statistics.mean(b["days"]), 2)
        b["n_days"] = len(b["days"])
        del b["days"]

    def band_id_of(day):
        for b in bands:
            if b["start"] <= day <= b["end"]:
                return b["id"]
        return None

    # 升降点
    cand = []
    for i in range(window, len(ann)):
        d = ann[i]["trend"] - ann[i - window]["trend"]
        if abs(d) >= 1.0:
            trig = None
            for r in reversed(rows):
                if r["ts"] < ann[i]["ts"] and r["who"] != other:
                    trig = {"time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"), "text": r["txt"][:60]}
                    break
            cand.append({"time": ann[i]["time"], "dir": "up" if d > 0 else "down",
                         "delta": round(d, 2), "from_trend": ann[i - window]["trend"],
                         "to_trend": ann[i]["trend"], "her_text": ann[i]["text"],
                         "her_label": ann[i]["label_cn"], "trigger_by_self": trig})
    turns = []
    for c in cand:
        if turns and turns[-1]["dir"] == c["dir"] and \
                abs((datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S") -
                     datetime.strptime(turns[-1]["time"], "%Y-%m-%d %H:%M:%S")).total_seconds()) < 3600:
            if abs(c["delta"]) > abs(turns[-1]["delta"]):
                turns[-1] = c
            continue
        turns.append(c)
    turns.sort(key=lambda x: -abs(x["delta"]))

    # 给每一句补上"所在波段"与"当日趋势"
    day_trend = {d["day"]: d["trend"] for d in day_seq}
    for a in ann:
        a["band_id"] = band_id_of(a["time"][:10])
        a["day_trend"] = day_trend.get(a["time"][:10])

    daily = defaultdict(lambda: defaultdict(int))
    for a in ann:
        daily[a["time"][:10]][a["label_cn"]] += 1

    # 索引：idx -> 该条之前的 her trend（用于算"说完之后她怎么变"）
    return {"meta": {"annotated_msgs": len(ann), "window": window,
                     "note": "启发式词典标注，需人工复核；vulnerable 为开放信号非负面"},
            "timeline": [{k: v for k, v in a.items() if k != "ts"} for a in ann],
            "day_sequence": day_seq, "bands": bands,
            "top_up_turns": [t for t in turns if t["dir"] == "up"][:12],
            "top_down_turns": [t for t in turns if t["dir"] == "down"][:12],
            "daily_label_dist": {d: dict(v) for d, v in daily.items()},
            "_ann": ann, "_day_trend": day_trend, "_band_id_of": band_id_of, "_turns": turns}


# ---------- 方法③：差评/好评（逐句 + 效果值）----------
def classify_pb(txt):
    t = txt.strip()
    if not t:
        return None, None, []
    stripped = t
    for m in MEDIA:
        stripped = stripped.replace(m, "")
    if any(m in t for m in MEDIA) and (not stripped.strip(" 　") or "媒体" in stripped):
        return "share", "media", []
    if t.startswith("我") and ("笨" in t or "傻" in t):
        return None, "self_deprecate", []
    blame = has(t, CRITICIZE) or has(t, COMPLAIN_YOU)
    dem = has(t, DEMAND)
    if (blame or dem) and not any(s in t for s in BLAME_SAFE):
        return "blame", ("criticize" if blame else "demand"), ([w for w in CRITICIZE + COMPLAIN_YOU if w in t] +
                                                               [w for w in DEMAND if w in t])[:3]
    neg = [w for w in NEG_EVAL if w in t]
    if neg and "你" in t and not any(s in t for s in BLAME_SAFE):
        if "还是" in t or "？" in t or "?" in t:
            return None, "preference_q", []
        return "blame", "neg_eval", neg[:3]
    pr = [w for w in PRAISE if w in t]
    if pr and ("你" in t or "咱" in t or "我们" in t):
        body = t.replace("媒体：当前导出格式记录媒体状态，但不复制媒体文件", "")
        for m in MEDIA:
            body = body.replace(m, "")
        if not any("\u4e00" <= ch <= "\u9fff" for ch in body):
            return "share", "media", []
        return "praise", ("role" if has(t, PRAISE_ROLE) else "praise"), pr[:3]
    return None, None, []


def run_pb(rows, self_name, other, bandres, window=5):
    """逐句筛正负向，并计算**效果值**：说完这句之后，她的 trend 变了多少。"""
    ann = bandres["_ann"]
    day_trend = bandres["_day_trend"]
    bid = bandres["_band_id_of"]

    # next_her[i] = rows[i] 之后（含自身）第一条「她」的消息在 ann 中的下标。
    # 从后往前扫：遇到她的消息先把游标减 1（该条自身的 ann 下标），再赋给当前 i。
    next_her = [len(ann)] * len(rows)
    cur = len(ann)
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["who"] == other:
            cur -= 1
        next_her[i] = cur

    def effect(i, n=window):
        """该句之后 n 条她的消息 trend 均值 − 之前 n 条 trend 均值"""
        p = next_her[i]
        if p >= len(ann):
            return None
        after = [ann[j]["trend"] for j in range(p, min(len(ann), p + n))]
        start = max(0, p - n)
        before = [ann[j]["trend"] for j in range(start, p)]
        if not after or not before:
            return None
        return round(statistics.mean(after) - statistics.mean(before), 2)

    blame, praise, recv_neg, recv_pos, share = [], [], [], [], []
    for i, r in enumerate(rows):
        side, kind, ws = classify_pb(r["txt"])
        rec = {"time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "hits": ws,
               "text": r["txt"][:80], "len": len(r["txt"]),
               "band_id": bid(r["ts"].strftime("%Y-%m-%d")),
               "day_trend": day_trend.get(r["ts"].strftime("%Y-%m-%d"))}
        if side == "share":
            share.append(rec)
            continue
        if r["who"] == self_name:
            rec["effect_after"] = effect(i)     # ★ 说完之后她的 trend 变化
            if side == "blame":
                blame.append(rec)
            elif side == "praise":
                praise.append(rec)
        else:
            if side == "blame":
                recv_neg.append(rec)
            elif side == "praise":
                recv_pos.append(rec)

    def avg_effect(lst):
        vs = [x["effect_after"] for x in lst if x.get("effect_after") is not None]
        return round(statistics.mean(vs), 2) if vs else None

    return {"self_blame": {"n": len(blame),
                           "avg_effect": avg_effect(blame),
                           "items": sorted(blame, key=lambda x: (x.get("effect_after") is None,
                                                                 x.get("effect_after", 0)))},
            "self_praise": {"n": len(praise),
                            "avg_effect": avg_effect(praise),
                            "items": sorted(praise, key=lambda x: -(x.get("effect_after") or -99))},
            "other_neg": {"n": len(recv_neg), "items": recv_neg},
            "other_pos": {"n": len(recv_pos), "items": recv_pos},
            "share_media": {"n": len(share)},
            "pos_neg_ratio": round(len(praise) / len(blame), 2) if blame else None,
            "_note": "effect_after = 说完这句后 window 条她的 trend 均值 − 之前 window 条均值；"
                     "正数=她说完更热，负数=更冷。启发式，需人工复核。"}


# ---------- 方法②：VOL（事件锚点自动取自波段升降点）----------
def daily_stats(rows, other, self_name):
    per = defaultdict(list)
    prev = None
    lat = defaultdict(list)
    for r in rows:
        d = r["ts"].strftime("%Y-%m-%d")
        if r["who"] == other:
            per[d].append(r)
            if prev and prev["who"] == self_name:
                lat[d].append((r["ts"] - prev["ts"]).total_seconds())
        prev = r
    out = {}
    for d, ms in per.items():
        txt = " ".join(m["txt"] for m in ms)
        out[d] = {"msg": len(ms), "char": round(statistics.mean(len(m["txt"]) for m in ms), 1),
                  "q": sum(m["txt"].count("？") + m["txt"].count("?") + m["txt"].count("吗") for m in ms),
                  "pos": count_hits(txt, POS_W), "neg": count_hits(txt, NEG_W),
                  "hedge": count_hits(txt, HEDGE_W),
                  "low": sum(1 for m in ms if m["txt"].strip() in LOW_EFFORT),
                  "latency_median_sec": round(statistics.median(lat[d]), 1) if lat.get(d) else None}
    return out


def window_avg(stats, days, keys):
    acc = {k: [] for k in keys}
    for d in days:
        if d in stats:
            for k in keys:
                if stats[d][k] is not None:
                    acc[k].append(stats[d][k])
    return {k: (round(statistics.mean(v), 2) if v else None) for k, v in acc.items()}


def ratio(a, b):
    if a is None or b in (None, 0):
        return None
    return round(a / b, 2)


def run_vol(rows, other, self_name, bandres, manual_events=None, base_days=14):
    stats = daily_stats(rows, other, self_name)
    all_days = sorted(stats)
    keys = ["msg", "char", "q", "pos", "neg", "hedge", "low", "latency_median_sec"]

    # ★ 事件锚点自动取自波段升降点（Top N 升降点，按日期去重）
    if manual_events:
        events = [{"date": e["date"], "desc": e.get("desc", "手写事件"), "source": "manual"}
                  for e in manual_events]
    else:
        turns = bandres["_turns"][:14]
        seen, events = set(), []
        for t in turns:
            d = t["time"][:10]
            if d in seen:
                continue
            seen.add(d)
            events.append({"date": d,
                           "desc": f"自动锚点：{'升温' if t['dir']=='up' else '降温'}点 "
                                   f"Δ{t['delta']:+.2f}｜她[{t['her_label']}]「{t['her_text'][:26]}」",
                           "source": "auto_band_turn",
                           "trigger_by_self": t["trigger_by_self"]})

    out = []
    for e in events:
        d0 = datetime.strptime(e["date"], "%Y-%m-%d")
        base_d = [(d0 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, base_days + 1)]
        shock_d = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 2)]
        rec_d = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(2, 5)]
        base = window_avg(stats, base_d, keys)
        shock = window_avg(stats, shock_d, keys)
        rec = window_avg(stats, rec_d, keys)
        amp = {k: ratio(shock.get(k), base.get(k)) for k in ["msg", "char", "q", "neg", "hedge", "latency_median_sec"]}
        rec_v = {k: ratio(rec.get(k), base.get(k)) for k in ["msg", "char", "neg"]}
        # 样本量门控：低量日（冲击窗她说话太少）时，量降与时延升都是噪声，不判方向。
        # ★ 这是自动锚点带来的新问题——手写事件清单时不会遇到，必须门控。
        shock_n = sum(stats[d]["msg"] for d in shock_d if d in stats)
        low_sample = shock_n < 8
        # 方向判定（金标准：看时延）
        m, l, q = amp.get("msg"), amp.get("latency_median_sec"), amp.get("q")
        if low_sample:
            direction = "样本不足（不判）"
        elif m is None or l is None:
            direction = "数据不足"
        elif m >= 1.15 and (l is None or l <= 1.2):
            direction = "向热"
        elif m < 1.0 and l >= 1.4 and (q or 1) < 0.8:
            direction = "抽离（危险）"
        elif m < 1.0 and l < 1.4:
            direction = "压力缩量"
        elif m >= 1.0 and l >= 1.4:
            direction = "黏着犹豫"
        else:
            direction = "微动"
        # 窗内波段标签分布（逐句复核用）
        labels = defaultdict(int)
        for a in bandres["_ann"]:
            if shock_d[0] <= a["time"][:10] <= shock_d[1]:
                labels[a["label_cn"]] += 1
        # ★ 自动锚点只是候选：低量日的「量降+时延升」极容易被误读成抽离，
        #   因此 auto 事件的危险型判定一律标 needs_confirm，必须人工确认为真事件后才采信。
        needs_confirm = (e["source"] == "auto_band_turn") or low_sample
        out.append({"date": e["date"], "desc": e["desc"], "source": e["source"],
                    "needs_confirm": needs_confirm,
                    "trigger_by_self": e.get("trigger_by_self"),
                    "baseline": base, "shock": shock, "recovery": rec,
                    "amp": amp, "recovery_vs_base": rec_v,
                    "direction": direction,
                    "shock_label_dist": dict(labels),
                    "shock_n_msgs": shock_n, "low_sample": low_sample,
                    "band_id": bandres["_band_id_of"](e["date"]),
                    "data_sufficient": bool(rec_d[-1] in stats)})
    return {"events": out, "_stats": stats}


# ---------- 方法④：异动（维度漂移 + 引文定位）----------
def run_drift(rows, other, self_name, bandres, split=0.67):
    """口径与 drift_analysis.py 完全一致（窗口内双方全部消息 → 只统计她的指标）。"""
    if not rows:
        return {}
    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    cut = t0 + (t1 - t0) * split

    def agg(msgs):
        o = [m for m in msgs if m["who"] == other]
        if not o:
            return None
        joined = " ".join(m["txt"] for m in o)
        lat, last = [], None
        for m in msgs:
            if last and last["who"] != m["who"] and m["who"] == other:
                d = (m["ts"] - last["ts"]).total_seconds()
                if 0 < d < 7200:
                    lat.append(d)
            last = m
        init_o = init_s = 0
        prev = None
        for m in msgs:
            if prev is None or (m["ts"] - prev).total_seconds() > 7200:
                if m["who"] == other:
                    init_o += 1
                else:
                    init_s += 1
            prev = m["ts"]
        days = max(1, len({m["ts"].date() for m in msgs}))
        return {"msg": len(o), "msg_per_day": round(len(o) / days, 2),
                "char_avg": round(sum(len(m["txt"]) for m in o) / len(o), 1),
                "question_ratio": round(sum(1 for m in o if any(w in m["txt"] for w in QUESTION_WORDS)) / len(o), 3),
                "pos": count_hits(joined, POSITIVE), "neg": count_hits(joined, NEGATIVE),
                "pos_neg_ratio": round(count_hits(joined, POSITIVE) / max(1, count_hits(joined, NEGATIVE)), 2),
                "hedge_per_msg": round(count_hits(joined, HEDGE) / len(o), 3),
                "low_effort_ratio": round(sum(1 for m in o if m["txt"].strip() in LOW_EFFORT) / len(o), 3),
                "night_ratio": round(sum(1 for m in o if m["ts"].hour >= 22 or m["ts"].hour < 6) / len(o), 3),
                "latency_median_sec": round(statistics.median(lat), 1) if lat else None,
                "initiative_share": round(init_o / max(1, init_o + init_s), 3),
                "n_sessions": init_o + init_s}

    base_msgs = [m for m in rows if m["ts"] < cut]
    recent_msgs = [m for m in rows if m["ts"] >= cut]
    base, rec = agg(base_msgs), agg(recent_msgs)
    _DRIFT_KEYS = ["msg_per_day", "char_avg", "question_ratio", "pos_neg_ratio", "hedge_per_msg",
                   "low_effort_ratio", "night_ratio", "latency_median_sec", "initiative_share"]
    # ★ 优雅降级：基线窗或近期窗没有对方消息时，agg() 返回 None。
    #   此时不崩溃，drift 全置 None 并在 meta 说明原因（样本不足无法比较）。
    if base and rec:
        drift = {k: ratio(rec.get(k), base.get(k)) for k in _DRIFT_KEYS}
    else:
        drift = {k: None for k in _DRIFT_KEYS}

    monthly = defaultdict(list)
    for m in rows:
        monthly[m["ts"].strftime("%Y-%m")].append(m)
    monthly = {k: v2 for k, v2 in ((k, agg(v)) for k, v in sorted(monthly.items())) if v2}
    # ★ 近期窗内她的句子；无则用全部她的句子兜底（避免引文归组为空）
    rec_rs = [r for r in recent_msgs if r["who"] == other] or [r for r in rows if r["who"] == other]

    # ★ 质性引文定位：把最近窗内她的句子按波段归组，供人工挑引文（★ 每条必带 who）
    bid = bandres["_band_id_of"]
    recent_quotes = defaultdict(list)
    for r in rec_rs:
        recent_quotes[bid(r["ts"].strftime("%Y-%m-%d")) or "-"].append(
            {"who": r["who"], "date": r["ts"].strftime("%Y-%m-%d"), "text": r["txt"][:50]})
    return {"baseline_window": [t0.strftime("%Y-%m-%d %H:%M:%S"), cut.strftime("%Y-%m-%d %H:%M:%S")],
            "recent_window": [cut.strftime("%Y-%m-%d %H:%M:%S"), t1.strftime("%Y-%m-%d %H:%M:%S")],
            "baseline": base, "recent": rec, "drift_recent_vs_base": drift,
            "monthly": monthly, "recent_quotes_by_band": {k: v[:12] for k, v in recent_quotes.items()}}


# ---------- 交叉验证 ----------
def cross_check(bandres, volres, driftres, pbres, qualres=None):
    """三法结论冲突时自动列出，供人工复核。"""
    issues = []
    day_trend = bandres["_day_trend"]
    for e in volres["events"]:
        d = e["date"]
        bt = day_trend.get(d)
        if bt is None:
            continue
        band_dir = "up" if bt >= 0.6 else ("down" if bt <= -0.4 else "flat")
        vol_dir = e["direction"]
        if band_dir == "down" and vol_dir in ("向热", "黏着犹豫"):
            issues.append({"type": "波段↓ 但 VOL 判向热", "date": d,
                           "band_trend": bt, "vol_direction": vol_dir,
                           "hint": "可能：窗内量升但心态标签偏负（说得多但内容冷）。以原文为准。"})
        if band_dir == "up" and vol_dir in ("抽离（危险）", "压力缩量"):
            issues.append({"type": "波段↑ 但 VOL 判缩量/抽离", "date": d,
                           "band_trend": bt, "vol_direction": vol_dir,
                           "hint": "可能：心态热但表达量降。以时延为准再看一次。"})
    # auto 事件被判危险 → 疑似噪声（需人工确认）
    for e in volres["events"]:
        if e.get("needs_confirm") and e["direction"] in ("抽离（危险）", "压力缩量", "黏着犹豫"):
            issues.append({"type": "自动锚点被判负向（疑似噪声）", "date": e["date"],
                           "direction": e["direction"], "shock_n_msgs": e.get("shock_n_msgs"),
                           "hint": "锚点来自波段拐点而非真实事件，低量日易误判。确认是否为真事件后再采信。"})
    # 差评效果 vs 好评效果
    be, pe = pbres["self_blame"].get("avg_effect"), pbres["self_praise"].get("avg_effect")
    if be is not None and pe is not None and pe <= be:
        issues.append({"type": "好评效果不高于差评", "blame_effect": be, "praise_effect": pe,
                       "hint": "好评可能不够具体或时机不对（PR1/PR5），或差评其实被接住了。人工复核原文。"})
    # 异动 vs 波段
    if driftres:
        init_d = driftres["drift_recent_vs_base"].get("initiative_share")
        mpd_d = driftres["drift_recent_vs_base"].get("msg_per_day")
        if init_d is not None and mpd_d is not None and init_d <= 0.7 and mpd_d >= 1.0:
            issues.append({"type": "发起占比降但条数升", "initiative_drift": init_d,
                           "msg_per_day_drift": mpd_d,
                           "hint": "典型补位效应（D 类：你开口变多挤掉了她）。先查自己的发起占比。"})
    # 质性维度：某维度"消失"或"显著下降" → 值得查是不是被你挤掉了 / 你踩了禁区
    if qualres:
        for dim, d in qualres["dims"].items():
            if d["state"].startswith("消失"):
                issues.append({"type": "质性维度消失", "dim": dim,
                               "baseline": d["baseline"]["per_1k"], "recent": d["recent"]["per_1k"],
                               "hint": "一类话题彻底不提了。查是不是你踩过禁区、或话题被你接走了。"})
    return issues


def main():
    argv = sys.argv[1:]
    # ★ 兼容两种 CLI：具名 --input 或 位置参数
    if "--input" in argv:
        path = argv[argv.index("--input") + 1]
    elif "-i" in argv:
        path = argv[argv.index("-i") + 1]
    else:
        path = argv[0]
    time_unreliable = "--time-unreliable" in argv
    # 位置参数模式下，--self 缺失时不再默认"清峰"，要求显式给出（schema 2.0 要求）
    self_name, other, window, split = None, None, 5, 0.67
    if "--self" in argv:
        self_name = argv[argv.index("--self") + 1]
    if "--other" in argv:
        other = argv[argv.index("--other") + 1]
    if "--window" in argv:
        window = int(argv[argv.index("--window") + 1])
    if "--split" in argv:
        split = float(argv[argv.index("--split") + 1])
    manual = None
    if "--events" in argv:
        manual = json.load(open(argv[argv.index("--events") + 1], encoding="utf-8"))
    if not self_name:
        raise SystemExit("必须用 --self 指定本人昵称（不再隐式默认）")

    # 输出目录：默认写输入文件所在目录，避免从 skill 目录运行时污染 skill
    if "--outdir" in argv:
        outdir = argv[argv.index("--outdir") + 1]
    else:
        outdir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(outdir, exist_ok=True)
    op = lambda name: os.path.join(outdir, name)

    rows = parse(path, time_unreliable)
    b = run_band(rows, other, window)
    p = run_pb(rows, self_name, other, b, window)
    v = run_vol(rows, other, self_name, b, manual)
    d = run_drift(rows, other, self_name, b, split)
    # 质性维度异动（价值观/三观/性格/兴趣/谈话风格）：记录要带说话人
    q = None
    if DQ:
        q = DQ.scan(rows, "other", self_name, other, split,
                    band_of=lambda day: b["_band_id_of"](day))
    x = cross_check(b, v, d, p, q)

    out = {"meta": {"self": self_name, "other": other, "window": window, "split": split,
                    "total_msgs": len(rows),
                    "span": [rows[0]["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                             rows[-1]["ts"].strftime("%Y-%m-%d %H:%M:%S")] if rows else None,
                    "methods": ["心态波段（逐句）", "VOL（事件窗）", "差评/好评（逐句）", "异动（维度）"],
                    "note": "四个方法共享同一份逐句时间轴；标注为启发式词典，需人工复核"},
           "band": {k: v2 for k, v2 in b.items() if not k.startswith("_")},
           "pb": p, "vol": {"events": v["events"]},
           "drift": {k: v2 for k, v2 in d.items() if k != "recent_quotes_by_band"} | {"recent_quotes_by_band": d.get("recent_quotes_by_band", {})},
           "drift_qual": q,
           "cross_check": x}
    json.dump(out, open(op("analysis_all.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ★ 兼容写出：单跑脚本的同名 json（band / vol_events / drift / pb），供案例级报告生成器
    #   （如工作区根目录的 build_retro.py）直接消费——避免「统一入口」与「四文件管线」两条数据链路分裂。
    #   结构与各单跑脚本一致：band.json=bands/timeline/…、vol_events.json={events}、drift.json={drift,…}、pb.json=self_blame/self_praise/…。
    json.dump({k: v for k, v in b.items() if not k.startswith("_")},
              open(op("band.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"events": v["events"]},
              open(op("vol_events.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(d, open(op("drift.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(p, open(op("pb.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if q:
        json.dump(q, open(op("drift_qual.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 控制台摘要
    print("=== 统一分析（一次读取 · 四方法并行）===")
    print(f"消息 {len(rows)} 条｜被标注（她）{b['meta']['annotated_msgs']} 条")
    print(f"\n[① 波段] {len(b['bands'])} 段，拐点 {len(b['_turns'])} 处")
    for bb in b["bands"]:
        print(f"   {bb['id']} {bb['level']:4s} {bb['start']}~{bb['end']} "
              f"{bb['n_days']:2d}天/{bb['n_msgs']:3d}条 trend={bb['trend_avg']:+.2f}")
    print(f"\n[② VOL] 事件 {len(v['events'])} 个（{'自动取自波段升降点' if not manual else '手写清单'}）")
    for e in v["events"][:8]:
        flag = "⚠待确认" if e.get("needs_confirm") else "  "
        print(f"   {e['date']} {e['direction']:8s} 量{e['amp'].get('msg')}× "
              f"时延{e['amp'].get('latency_median_sec')}× {flag}｜{e['desc'][:36]}")
    def fmt(v):
        return f"{v:+.2f}" if v is not None else "n/a"
    print(f"\n[③ 差评/好评] 差评 {p['self_blame']['n']}（说完她 trend {fmt(p['self_blame']['avg_effect'])}） ｜"
          f"好评 {p['self_praise']['n']}（说完她 trend {fmt(p['self_praise']['avg_effect'])}）")
    if p["self_blame"]["items"]:
        print("   最伤的一句：" + p["self_blame"]["items"][0]["text"][:40] +
              f"（effect {p['self_blame']['items'][0].get('effect_after')}）")
    if p["self_praise"]["items"]:
        print("   最有效的一句：" + p["self_praise"]["items"][0]["text"][:40] +
              f"（effect {p['self_praise']['items'][0].get('effect_after')}）")
    print(f"\n[④ 异动 · 量化] drift：" + "｜".join(f"{k}={val}" for k, val in d.get("drift_recent_vs_base", {}).items()))
    if q:
        print("\n[④ 异动 · 质性维度]（价值观/三观/性格/兴趣/谈话风格）")
        for dim, dd in sorted(q["dims"].items(), key=lambda kv: kv[1]["state"]):
            if dd["state"] == "未出现" or dd["state"] == "平稳":
                continue
            dr = f"{dd['drift']}×" if dd["drift"] is not None else "—"
            print(f"   [{dd['state']}] {dim}  {dd['baseline']['per_1k']} → {dd['recent']['per_1k']} /千条 ({dr})")
            for qq in dd["recent"]["quotes"][:1]:
                print(f"        {qq['who']}·{qq['date']}：「{qq['text'][:40]}」")
    print(f"\n[交叉验证] {len(x)} 条待人工复核")
    for xx in x:
        print(f"   ⚠ {xx['type']} — {xx.get('hint','')[:50]}")
    print(f"\n已写出：{op('analysis_all.json')}")
    print(f"    （同目录另写出 band.json / vol_events.json / drift.json / pb.json{' / drift_qual.json' if q else ''}）")


if __name__ == "__main__":
    main()
