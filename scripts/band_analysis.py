# -*- coding: utf-8 -*-
"""
心态波段分析法（逐句扫描层）：
对聊天记录**每一句**打心态标签 → 形成时间轴 → 识别波段（升温/平台/回落/防御）
→ 标出**升级点与降点** → 给出触发句（候选归因）与应对提示。

标签与分值（启发式词典，需人工复核）：
  enthusiasm 热忱 +2 ｜ agreement 同意 +1 ｜ neutral 中性 0 ｜ probe 试探 0
  vulnerable 脆弱 -1（开放信号，需接住）｜ defensive 防御 -1 ｜ avoidance 回避 -1 ｜ resistance 抗拒 -2

用法：
  python band_analysis.py <chat.txt> --self 清峰 --other 柒月 [--window 5]
输出：band.json（逐句标注 / 波段 / 升降点 / 每日分布）
"""
import sys, json, re, statistics
from datetime import datetime

ENTHUSIASM = ["哈哈","哈哈哈","嘿嘿","嘻嘻","开心","高兴","喜欢","好呀","好啊","可以呀","期待","想你","爱你",
              "么么","抱抱","棒","厉害","可爱","甜","惊喜","漂亮","好看","太好了","真好","一起","陪你","记得"]
AGREE = ["好的","可以","行","ok","OK","嗯嗯","收到","都行","你定","听你的","没问题","好呢","好的呀"]
DEFENSE = ["不是","没有啊","哪有","怎么会","你别","我哪","才不是","开玩笑啦","你想多了","瞎说","才没有"]
RESIST = ["不行","不方便","算了","不用了","改天","再说吧","下次吧","不要","别了","我不了","不去","不行了"]
AVOID = ["嗯","哦","噢","好吧","随便","看你","都好","先这样","在忙","忙","晚点","回头","都可以"]
VULNERABLE = ["累","好累","难过","难受","委屈","崩溃","压力","焦虑","失眠","emo","不舒服","烦","疼","难受死了"]
PROBE_MARK = ["是不是","你觉得","你觉得呢","应该","要是","如果","会不会","真的吗","?","？","吗","呢"]
MEDIA = ["[图片]","[照片]","[视频]","[表情]","[动画表情]","[语音]"]

LINE_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>[^:：]+)[:：](?P<txt>.*)$")

LABEL_SCORE = {"enthusiasm": 2, "agreement": 1, "neutral": 0, "probe": 0,
               "vulnerable": -1, "defensive": -1, "avoidance": -1, "resistance": -2}
LABEL_CN = {"enthusiasm": "热忱", "agreement": "同意", "neutral": "中性", "probe": "试探",
            "vulnerable": "脆弱", "defensive": "防御", "avoidance": "回避", "resistance": "抗拒"}


def has(t, ws):
    return any(w in t for w in ws)


def label_of(txt):
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
        return "enthusiasm"          # 主动发图/表情＝分享意愿
    if has(t, PROBE_MARK):
        return "probe"
    if t in AVOID or (len(t) <= 2 and has(t, AVOID)):
        return "avoidance"
    return "neutral"


def main():
    path = sys.argv[1]
    self_name, other_name, window = "清峰", "柒月", 5
    if "--self" in sys.argv:
        self_name = sys.argv[sys.argv.index("--self") + 1]
    if "--other" in sys.argv:
        other_name = sys.argv[sys.argv.index("--other") + 1]
    if "--window" in sys.argv:
        window = int(sys.argv[sys.argv.index("--window") + 1])

    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        raw = m.group("ts").replace("-", "/")
        ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
        rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])

    # 1) 逐句标注（只对"对方"；同时保留"我"的句子用于归因）
    ann = []
    for r in rows:
        if r["who"] != other_name:
            continue
        lb = label_of(r["txt"])
        ann.append({"time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                    "label": lb, "label_cn": LABEL_CN[lb],
                    "score": LABEL_SCORE[lb], "text": r["txt"][:60]})

    # 2) 平滑趋势（滚动窗口均值）
    for i, a in enumerate(ann):
        win = ann[max(0, i - window + 1): i + 1]
        a["trend"] = round(sum(x["score"] for x in win) / len(win), 2)

    # 3) 波段划分：先按日聚合（逐句分段过于碎片化），再按日级别切段
    per_day = {}
    for a in ann:
        per_day.setdefault(a["time"][:10], []).append(a["trend"])
    day_seq = [{"day": d, "trend": round(statistics.mean(v), 2), "n": len(v)}
               for d, v in sorted(per_day.items())]
    bands, cur = [], None
    for dd in day_seq:
        lvl = ("up" if dd["trend"] >= 0.6 else "down" if dd["trend"] <= -0.4 else "flat")
        if cur and cur["level"] == lvl:
            cur["end"] = dd["day"]
            cur["n_msgs"] += dd["n"]
            cur["days"].append(dd["trend"])
        else:
            if cur:
                bands.append(cur)
            cur = {"level": lvl, "start": dd["day"], "end": dd["day"],
                   "n_msgs": dd["n"], "days": [dd["trend"]]}
    if cur:
        bands.append(cur)
    for b in bands:
        b["trend_avg"] = round(statistics.mean(b["days"]), 2)
        b["n_days"] = len(b["days"])
        del b["days"]

    # 4) 升降点：跨窗口比较（当前条 vs window 条之前），取变化最大的若干处
    cand = []
    for i in range(window, len(ann)):
        d = ann[i]["trend"] - ann[i - window]["trend"]
        if abs(d) >= 1.0:
            t_i = datetime.strptime(ann[i]["time"], "%Y-%m-%d %H:%M:%S")
            trigger = None
            for r in reversed(rows):
                if r["ts"] < t_i and r["who"] == self_name:
                    trigger = {"time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"), "text": r["txt"][:60]}
                    break
            cand.append({"time": ann[i]["time"],
                         "dir": "up" if d > 0 else "down",
                         "delta": round(d, 2),
                         "from_trend": ann[i - window]["trend"], "to_trend": ann[i]["trend"],
                         "her_text": ann[i]["text"], "her_label": ann[i]["label_cn"],
                         "trigger_by_self": trigger})
    # 只保留局部极值（相邻 3 条内不重复取同向点）
    turns, last_up, last_dn = [], None, None
    for c in cand:
        if c["dir"] == "up":
            if last_up and (len(turns) and turns[-1]["dir"] == "up" and
                            abs((datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S") -
                                 datetime.strptime(turns[-1]["time"], "%Y-%m-%d %H:%M:%S")).total_seconds()) < 3600):
                if c["delta"] > turns[-1]["delta"]:
                    turns[-1] = c
                continue
            turns.append(c)
        else:
            if len(turns) and turns[-1]["dir"] == "down" and \
                    abs((datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S") -
                         datetime.strptime(turns[-1]["time"], "%Y-%m-%d %H:%M:%S")).total_seconds()) < 3600:
                if c["delta"] < turns[-1]["delta"]:
                    turns[-1] = c
                continue
            turns.append(c)
    turns.sort(key=lambda x: -abs(x["delta"]))

    # 5) 每日标签分布
    daily = {}
    for a in ann:
        d = a["time"][:10]
        daily.setdefault(d, {})
        daily[d][a["label_cn"]] = daily[d].get(a["label_cn"], 0) + 1

    out = {"meta": {"self": self_name, "other": other_name, "window": window,
                    "annotated_msgs": len(ann),
                    "note": "启发式词典标注，需人工复核；vulnerable 为开放信号非负面"},
           "timeline": ann, "day_sequence": day_seq, "bands": bands,
           "top_up_turns": [t for t in turns if t["dir"] == "up"][:12],
           "top_down_turns": [t for t in turns if t["dir"] == "down"][:12],
           "daily_label_dist": daily}
    json.dump(out, open("band.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== 心态波段分析（逐句）===")
    print(f"标注消息 {len(ann)} 条｜波段 {len(bands)} 段｜升降点 {len(turns)} 处")
    print("\n[波段]")
    for b in bands:
        print(f"  {b['level']:4s} {b['start']} ~ {b['end']}  days={b['n_days']:2d} msgs={b['n_msgs']:3d} trend={b['trend_avg']}")
    print("\n[Top 升温点]")
    for t in [x for x in turns if x["dir"] == "up"][:12]:
        trig = t["trigger_by_self"]["text"] if t["trigger_by_self"] else "-"
        print(f"  {t['time'][:16]} {t['delta']:+.2f} ({t['from_trend']}->{t['to_trend']}) "
              f"她[{t['her_label']}]「{t['her_text'][:24]}」｜他触发「{trig[:24]}」")
    print("\n[Top 降温点]")
    for t in [x for x in turns if x["dir"] == "down"][:12]:
        trig = t["trigger_by_self"]["text"] if t["trigger_by_self"] else "-"
        print(f"  {t['time'][:16]} {t['delta']:+.2f} ({t['from_trend']}->{t['to_trend']}) "
              f"她[{t['her_label']}]「{t['her_text'][:24]}」｜他触发「{trig[:24]}」")
    print("\n已写出 band.json")


if __name__ == "__main__":
    main()
