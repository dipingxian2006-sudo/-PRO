# -*- coding: utf-8 -*-
"""
需求异动（选品）法 —— 维度漂移检测：
从聊天记录里「选」出她的稳定属性（价值观/性格/兴趣/谈话风格等维度），
建立基线，再检测各维度随时间的**异动（漂移）**，供归因与应对使用。

用法：
  python drift_analysis.py <chat.txt> --self 清峰 --other 柒月 [--split 0.67]

输出：drift.json（基线窗 / 近期窗 / 各维度值与漂移倍数 / 按月序列）
"""
import sys, json, re, statistics
from datetime import datetime
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


def hits(t, ws):
    return sum(t.count(w) for w in ws)


def main():
    path = sys.argv[1]
    self_name = "清峰"
    other_name = "柒月"
    split = 0.67
    if "--self" in sys.argv:
        self_name = sys.argv[sys.argv.index("--self") + 1]
    if "--other" in sys.argv:
        other_name = sys.argv[sys.argv.index("--other") + 1]
    if "--split" in sys.argv:
        split = float(sys.argv[sys.argv.index("--split") + 1])

    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        raw = m.group("ts").replace("-", "/")
        ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
        rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])

    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    cut = t0 + (t1 - t0) * split

    def agg(msgs, other=other_name, selfn=self_name):
        o = [m for m in msgs if m["who"] == other]
        if not o:
            return None
        joined = " ".join(m["txt"] for m in o)
        # 回复时延（她回复他）
        lat, last = [], None
        for m in msgs:
            if last and last["who"] != m["who"] and m["who"] == other:
                d = (m["ts"] - last["ts"]).total_seconds()
                if 0 < d < 7200:
                    lat.append(d)
            last = m
        # 会话段发起（间隔 >120min 视为新段）
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
        return {
            "msg": len(o),
            "msg_per_day": round(len(o) / days, 2),
            "char_avg": round(sum(len(m["txt"]) for m in o) / len(o), 1),
            "question_ratio": round(sum(1 for m in o if any(w in m["txt"] for w in QUESTION_WORDS)) / len(o), 3),
            "pos": hits(joined, POSITIVE),
            "neg": hits(joined, NEGATIVE),
            "pos_neg_ratio": round(hits(joined, POSITIVE) / max(1, hits(joined, NEGATIVE)), 2),
            "hedge_per_msg": round(hits(joined, HEDGE) / len(o), 3),
            "low_effort_ratio": round(sum(1 for m in o if m["txt"].strip() in LOW_EFFORT) / len(o), 3),
            "night_ratio": round(sum(1 for m in o if m["ts"].hour >= 22 or m["ts"].hour < 6) / len(o), 3),
            "latency_median_sec": round(statistics.median(lat), 1) if lat else None,
            "initiative_share": round(init_o / max(1, init_o + init_s), 3),
            "n_sessions": init_o + init_s,
        }

    base_msgs = [m for m in rows if m["ts"] < cut]
    recent_msgs = [m for m in rows if m["ts"] >= cut]
    base, recent = agg(base_msgs), agg(recent_msgs)

    def drift(a, b):
        """b 相对 a 的倍数；None 表示不可算"""
        out = {}
        for k in a:
            if k in ("pos", "neg", "msg", "n_sessions"):
                continue
            av, bv = a[k], b[k]
            if av in (None, 0) or bv is None:
                out[k] = None
            else:
                out[k] = round(bv / av, 2)
        return out

    # 按月序列
    monthly = defaultdict(list)
    for m in rows:
        monthly[m["ts"].strftime("%Y-%m")].append(m)
    series = {k: agg(v) for k, v in sorted(monthly.items())}

    result = {
        "meta": {"self": self_name, "other": other_name, "from": str(t0), "to": str(t1),
                 "cut": str(cut), "split": split},
        "baseline_window": base,
        "recent_window": recent,
        "drift_recent_vs_base": drift(base, recent),
        "monthly": series,
    }
    json.dump(result, open("drift.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== 需求异动（选品）法 · 维度漂移 ===")
    print(f"基线窗 {t0} ~ {cut}｜近期窗 {cut} ~ {t1}")
    print("\n[基线]", base)
    print("\n[近期]", recent)
    print("\n[漂移 = 近期 ÷ 基线]")
    for k, v in result["drift_recent_vs_base"].items():
        print(f"  {k}: {v}")
    print("\n[按月]")
    for k, v in series.items():
        print(f"  {k}: char_avg={v['char_avg']} q={v['question_ratio']} pos_neg={v['pos_neg_ratio']} "
              f"lat={v['latency_median_sec']} mpd={v['msg_per_day']} init={v['initiative_share']}")
    print("\n已写出 drift.json")


if __name__ == "__main__":
    main()
