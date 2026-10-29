# -*- coding: utf-8 -*-
"""
异动分析法 · **质性维度**漂移检测（价值观 / 三观 / 性格 / 兴趣 / 谈话风格 / 亲密表达 / 边界）

为什么需要这个脚本：
  drift_analysis.py 只算 9 个**量化**指标（条数、句长、提问率、时延…），
  回答不了「她的价值观/性格/兴趣/谈话风格往哪变了」。本脚本补上这一层：
  为每个质性维度建立**主题词表** → 统计基线窗/近期窗的提及密度（每千条）→
  算 drift → 判定「新出现 / 消失 / 上升 / 下降 / 平稳」→ 附**带说话人**的代表引文。

★ 引文硬要求：每条引文必须带 who（谁说的）、date、band_id。
  报告里引用任何一句话，都必须能回答「这是谁说的」。

用法：
  python drift_qual.py <chat.txt> --self 清峰 --other 柒月 [--split 0.67] [--side other]
  --side: other（只看她，默认）/ self（只看他）/ both
输出：drift_qual.json
"""
import sys, json, re
from datetime import datetime
from collections import defaultdict

LINE_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>[^:：]+)[:：](?P<txt>.*)$")

# ============ 维度词表（按 persona-engine 骨架 + 漂移敏感维度；可按需扩充）============
DIMS = {
    "价值观·金钱观": ["钱", "贵", "便宜", "价格", "AA", "aa", "买单", "请客", "工资", "报销",
                 "房贷", "物业费", "成本", "省钱", "划算", "一平", "房租", "分账"],
    "价值观·家庭观": ["爸妈", "父母", "我妈", "我爸", "家里", "家人", "孩子", "小孩", "回家",
                 "团圆", "中秋", "老人", "亲戚"],
    "价值观·婚姻观": ["结婚", "离婚", "前夫", "前妻", "婚姻", "夫妻", "领证", "嫁", "娶",
                 "二婚", "婚"],
    "价值观·忠诚与边界": ["专一", "唯一", "聊骚", "暧昧", "越界", "吃醋", "背叛", "异性",
                   "边界", "认真相处", "彼此", "出轨", "忠诚"],
    "价值观·工作伦理": ["加班", "领导", "同事", "汇报", "项目", "绩效", "辞职", "跳槽",
                 "努力", "责任", "上班", "下班", "工作", "老板", "客户"],

    "性格·主动性": ["我请你", "我来", "一起", "要不要", "走吧", "去不去", "明天有空",
                "我提议", "咱去", "我安排", "我订"],
    "性格·示弱与依靠": ["累", "撑不住", "帮帮我", "罩", "带带我", "我不行", "靠你",
                 "我怕", "救我", "你帮", "想靠"],
    "性格·幽默感": ["哈哈", "逗", "搞笑", "冷笑话", "段子", "皮", "贫", "笑死", "乐"],
    "性格·焦虑水平": ["担心", "怕", "慌", "想多", "失眠", "纠结", "不安", "确定吗",
                 "是不是", "会不会", "在想", "睡不着", "焦虑"],
    "性格·直球坦率": ["直说", "坦白", "直接", "明说", "不绕", "直球", "说实话", "摊开"],

    "兴趣·吃什么": ["吃", "餐", "饭", "店", "火锅", "烧烤", "奶茶", "喜茶", "必胜客",
                "外卖", "小吃", "餐厅", "好吃", "菜单"],
    "兴趣·玩什么": ["玩", "逛", "去哪", "旅游", "爬山", "荡秋千", "看电影", "散步",
                "兜风", "走走", "景区", "公园", "海边"],
    "兴趣·关注内容": ["视频", "抖音", "剧", "书", "歌", "新闻", "探店", "博主", "直播",
                 "小红书", "公众号"],
    "兴趣·消费取向": ["买", "下单", "购物车", "品牌", "护肤品", "医美", "水光", "玻尿酸",
                 "衣服", "穿搭", "包", "化妆品", "做脸", "皮肤"],

    "谈话风格·称呼": ["杜总", "小杜", "杜生", "宝贝", "黄总", "哥", "姐", "亲爱的"],
    "谈话风格·暧昧度": ["想你", "喜欢你", "爱你", "亲", "抱抱", "么么", "吻", "心动"],
    "谈话风格·肢体话题": ["牵手", "抱", "亲", "上手", "靠近", "摸", "挽", "搂", "靠肩"],
    "谈话风格·玩笑与调侃": ["开玩笑", "逗你", "骗人", "套路", "忽悠", "调侃", "损"],

    "亲密表达·未来指向": ["以后", "将来", "我们", "下次", "未来", "到时候", "以后我们",
                   "一辈子", "长期"],
    "亲密表达·承诺": ["答应", "保证", "一定", "认真", "约定", "说好了", "不骗你"],

    "边界·拒绝方式": ["不行", "不方便", "算了", "改天", "再说", "不用", "不了", "别了",
                 "下次吧", "我不了"],
    "自我暴露·过去经历": ["以前", "曾经", "那时候", "过去", "当时我", "我之前", "经历"],
}

# 排除词：防词义误匹配（同形不同义）。例：「玩手机」「玩玩」不是「兴趣·玩什么」。
EXCLUDE = {
    "兴趣·玩什么": ["玩手机", "玩玩", "玩笑", "玩不起", "玩命", "玩得开"],
    "兴趣·吃什么": ["吃亏", "吃醋", "吃惊", "吃苦", "吃不消"],
    "价值观·婚姻观": ["婚庆", "婚宴"],
    "性格·示弱与依靠": ["靠边", "靠不住"],
    "亲密表达·未来指向": ["我们公司", "我们领导", "我们部门", "我们团队"],
    "价值观·工作伦理": ["工作量", "工作证"],
}

# 只统计「她」的维度时，以下维度更该看她的表达
DEFAULT_SIDE = "other"

UP_TH, DOWN_TH = 1.5, 0.67     # 质性维度样本小，阈值比量化宽松一档
LOW_SAMPLE_HITS = 3            # 基线或近期命中 < 3 条 → 标低样本，不得当结论主证


def parse(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        raw = m.group("ts").replace("-", "/")
        ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
        rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])
    return rows


def scan(rows, side, self_name, other_name, split=0.67, band_of=None):
    target = other_name if side == "other" else self_name
    pool = [r for r in rows if side == "both" or r["who"] == target]
    if not pool:
        return None
    t0, t1 = pool[0]["ts"], pool[-1]["ts"]
    cut = t0 + (t1 - t0) * split
    base_rs = [r for r in pool if r["ts"] < cut]
    rec_rs = [r for r in pool if r["ts"] >= cut]

    def stat(rs, dim_words, dim=None):
        n = len(rs)
        if not n:
            return {"n_msgs": 0, "hits": 0, "per_1k": 0.0, "quotes": []}
        ex = EXCLUDE.get(dim, [])
        hits, quotes = 0, []
        for r in rs:
            if any(w in r["txt"] for w in dim_words) and not (ex and any(w in r["txt"] for w in ex)):
                hits += 1
                quotes.append({"who": r["who"],           # ★ 说话人
                               "date": r["ts"].strftime("%Y-%m-%d"),
                               "time": r["ts"].strftime("%Y-%m-%d %H:%M"),
                               "text": r["txt"][:70],
                               "band_id": band_of(r["ts"].strftime("%Y-%m-%d")) if band_of else None})
        return {"n_msgs": n, "hits": hits,
                "per_1k": round(hits / n * 1000, 1),
                "quotes": quotes[:6]}

    out = {}
    for dim, words in DIMS.items():
        b, r = stat(base_rs, words, dim), stat(rec_rs, words, dim)
        if b["per_1k"] == 0 and r["per_1k"] == 0:
            state, drift = "未出现", None
        elif b["per_1k"] == 0 and r["per_1k"] > 0:
            state, drift = "新出现", None
        elif b["per_1k"] > 0 and r["per_1k"] == 0:
            state, drift = "消失", 0.0
        else:
            drift = round(r["per_1k"] / b["per_1k"], 2)
            state = "显著上升" if drift >= UP_TH else ("显著下降" if drift <= DOWN_TH else "平稳")
        low = (b["hits"] < LOW_SAMPLE_HITS or r["hits"] < LOW_SAMPLE_HITS)
        if low and state in ("显著上升", "显著下降", "新出现", "消失"):
            state += "（低样本）"
        out[dim] = {"category": dim.split("·")[0],
                    "baseline": b, "recent": r, "drift": drift, "state": state,
                    "low_sample": low,
                    "words_hit": [w for w in words if any(w in q["text"] for q in r["quotes"])]}
    return {"side": side, "target": target,
            "baseline_window": [t0.strftime("%Y-%m-%d %H:%M:%S"), cut.strftime("%Y-%m-%d %H:%M:%S")],
            "recent_window": [cut.strftime("%Y-%m-%d %H:%M:%S"), t1.strftime("%Y-%m-%d %H:%M:%S")],
            "dims": out}


def main():
    path = sys.argv[1]
    self_name, other_name, split, side = "清峰", "柒月", 0.67, DEFAULT_SIDE
    if "--self" in sys.argv:
        self_name = sys.argv[sys.argv.index("--self") + 1]
    if "--other" in sys.argv:
        other_name = sys.argv[sys.argv.index("--other") + 1]
    if "--split" in sys.argv:
        split = float(sys.argv[sys.argv.index("--split") + 1])
    if "--side" in sys.argv:
        side = sys.argv[sys.argv.index("--side") + 1]

    rows = parse(path)
    res = scan(rows, side, self_name, other_name, split)
    json.dump(res, open("drift_qual.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"=== 质性维度异动（{'她' if side=='other' else ('他' if side=='self' else '双方')}）===")
    print(f"基线 {res['baseline_window'][0][:10]} ~ {res['baseline_window'][1][:10]} ｜"
          f"近期 {res['recent_window'][0][:10]} ~ {res['recent_window'][1][:10]}\n")
    order = ["新出现", "消失", "显著上升", "显著下降", "平稳", "未出现"]
    def key(kv):
        s = kv[1]["state"]
        base = next((i for i, o in enumerate(order) if s.startswith(o)), 9)
        return (base, -(kv[1]["recent"]["per_1k"]))
    items = sorted(res["dims"].items(), key=key)
    for dim, d in items:
        if d["state"] == "未出现":
            continue
        dr = f"{d['drift']}×" if d["drift"] is not None else "—"
        print(f"  [{d['state']}] {dim}  {d['baseline']['per_1k']} → {d['recent']['per_1k']} /千条  ({dr})")
        for q in d["recent"]["quotes"][:2]:
            print(f"        {q['who']}·{q['date']}：「{q['text'][:44]}」")
    print("\n已写出 drift_qual.json")


if __name__ == "__main__":
    main()
