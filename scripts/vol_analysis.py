# -*- coding: utf-8 -*-
"""
VOL（心态波动率 Volatility）分析：突发事件冲击下的心态响应。
与 volume_skew（量能偏斜，静态字数不对称）无关——VOL 看的是「事件冲击 → 偏离 → 回归」的动态过程。

用法：
  python vol_analysis.py <chat.txt> [--self 清峰] [--other 柒月]

输出：
  vol_daily.json  按日聚合
  vol_events.json 事件窗口冲击分析
"""
import sys, json, re, statistics
from datetime import datetime, timedelta
from collections import defaultdict

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

LINE_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>[^:：]+)[:：](?P<txt>.*)$")

def hits(text, words):
    return sum(text.count(w) for w in words)

def main():
    path = sys.argv[1]
    self_name = "清峰"
    other_name = "柒月"
    if "--self" in sys.argv:
        self_name = sys.argv[sys.argv.index("--self") + 1]
    if "--other" in sys.argv:
        other_name = sys.argv[sys.argv.index("--other") + 1]

    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            raw = m.group("ts").replace("-", "/")
            ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
            rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])

    # ---- 按日聚合 ----
    daily = defaultdict(lambda: {self_name: defaultdict(float), other_name: defaultdict(float)})
    lat = defaultdict(list)  # 她回复他的时延（按日）
    last_by = None
    last_ts = None
    for r in rows:
        d = r["ts"].date().isoformat()
        p = daily[d][r["who"]]
        p["msg"] += 1
        p["char"] += len(r["txt"])
        p["q"] += 1 if any(w in r["txt"] for w in QUESTION_WORDS) else 0
        p["pos"] += hits(r["txt"], POSITIVE)
        p["neg"] += hits(r["txt"], NEGATIVE)
        p["hedge"] += hits(r["txt"], HEDGE)
        p["low"] += 1 if r["txt"].strip() in LOW_EFFORT else 0
        if last_by and last_by != r["who"] and last_ts:
            delta = (r["ts"] - last_ts).total_seconds()
            if 0 < delta < 7200:
                lat[d].append(delta)
        last_by, last_ts = r["who"], r["ts"]

    daily_out = {}
    for d in sorted(daily):
        rec = {}
        for who in (self_name, other_name):
            p = daily[d][who]
            rec[who] = {
                "msg": int(p["msg"]), "char": int(p["char"]),
                "char_avg": round(p["char"] / p["msg"], 1) if p["msg"] else 0,
                "q": int(p["q"]), "pos": int(p["pos"]), "neg": int(p["neg"]),
                "hedge": int(p["hedge"]), "low": int(p["low"]),
            }
        rec["her_latency_median_sec"] = round(statistics.median(lat[d]), 1) if lat.get(d) else None
        daily_out[d] = rec
    json.dump(daily_out, open("vol_daily.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- 事件窗口分析 ----
    EVENTS = [
        ("E1", "2026-09-03", "她自述离婚史（前夫聊骚/负债/起诉离婚）——重大自我暴露"),
        ("E2", "2026-09-06", "她直球问结婚目的——关系定义冲击"),
        ("E3", "2026-09-15", "约定彼此唯一、认真一个月——承诺事件"),
        ("E4", "2026-09-21", "她说失眠、「在想你到底是个什么样的人」——焦虑峰值"),
        ("E5", "2026-09-23", "喜茶 + 浦西万达（最近一次线下见面）"),
        ("E6", "2026-09-25", "福州医美当日往返——身体 + 行程压力"),
        ("E7", "2026-09-26", "回家 + 闽超没票 + 朋友局——安排冲突日"),
    ]

    def window(dates):
        agg = {"msg": 0, "char": 0, "q": 0, "pos": 0, "neg": 0, "hedge": 0, "low": 0}
        lat_all = []
        for d in dates:
            if d in daily_out:
                p = daily_out[d][other_name]
                for k in agg:
                    agg[k] += p[k]
                if daily_out[d].get("her_latency_median_sec"):
                    lat_all.append(daily_out[d]["her_latency_median_sec"])
        n = max(1, len([d for d in dates if d in daily_out]))
        out = {k: round(v / n, 2) for k, v in agg.items()}
        out["latency_median_sec"] = round(statistics.median(lat_all), 1) if lat_all else None
        return out

    all_days = sorted(daily_out)
    results = []
    for code, date, desc in EVENTS:
        d0 = datetime.strptime(date, "%Y-%m-%d").date()
        # 基线：事件前 14 天（不含事件日及之后）
        base_days = [(d0 - timedelta(days=i)).isoformat() for i in range(1, 15)]
        base_days = [d for d in base_days if d in daily_out]
        # 冲击窗：事件日 + 次日
        shock_days = [(d0 + timedelta(days=i)).isoformat() for i in range(0, 2)]
        shock_days = [d for d in shock_days if d in daily_out]
        # 恢复窗：事件后 2–4 天
        rec_days = [(d0 + timedelta(days=i)).isoformat() for i in range(2, 5)]
        rec_days = [d for d in rec_days if d in daily_out]

        base = window(base_days)
        shock = window(shock_days)
        rec = window(rec_days) if rec_days else None

        def ratio(a, b, k):
            av, bv = a.get(k), b.get(k)
            if av is None or bv in (None, 0):
                return None
            return round(av / bv, 2)

        item = {
            "code": code, "date": date, "desc": desc,
            "base_days": len(base_days), "shock_days": len(shock_days),
            "baseline": base, "shock": shock, "recovery": rec,
            "amp": {
                "msg": ratio(shock, base, "msg"),
                "char": ratio(shock, base, "char"),
                "q": ratio(shock, base, "q"),
                "neg": ratio(shock, base, "neg"),
                "hedge": ratio(shock, base, "hedge"),
                "latency": ratio(shock, base, "latency_median_sec"),
            },
        }
        if rec:
            item["recovery_vs_base"] = {
                "msg": ratio(rec, base, "msg"),
                "char": ratio(rec, base, "char"),
                "neg": ratio(rec, base, "neg"),
            }
        results.append(item)

    json.dump(results, open("vol_events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 控制台摘要
    print("=== VOL 事件冲击（她：柒月）===")
    for it in results:
        print(f"\n{it['code']} {it['date']} {it['desc']}")
        print("  基线(前14天):", it["baseline"])
        print("  冲击(当日+次日):", it["shock"])
        print("  振幅:", it["amp"])
        if it.get("recovery_vs_base"):
            print("  恢复窗/基线:", it["recovery_vs_base"])
        else:
            print("  恢复窗: 数据不足")
    print("\n已写出 vol_daily.json / vol_events.json")

if __name__ == "__main__":
    main()
