# -*- coding: utf-8 -*-
"""
差评/好评分析法（表达层 · 逐句扫描）：
在心态波段分析（band_analysis.py）的同一份逐句时间轴上，筛出**指向对方**的
负向表达（吐槽 / 负面评价 / 直白批评 / 强要要求）与正向表达（表扬 / 肯定 / 示好），
并给出命中的风险点 TR / PR 与整改形态 A / G。

用法：
  python praise_blame_scan.py <chat.txt> --self 清峰 --other 柒月
输出：pb.json（差评清单 / 好评清单 / 接收侧清单 / 正负配比 / 命中统计）
"""
import sys, json, re
from datetime import datetime

LINE_RE = re.compile(r"^(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>[^:：]+)[:：](?P<txt>.*)$")

# —— 差评侧（指向对方的负向输出）——
CRITICIZE = ["你怎么", "你每次", "你总是", "你老是", "你就这样", "你这个人", "你这样不行", "你不对",
             "你太", "你别老", "你老是这样", "你能不能", "你怎么老是", "你咋"]
NEG_EVAL = ["不好", "不行", "不对", "太差", "不合适", "不靠谱", "离谱", "过分", "让人失望",
            "没意思", "无聊", "烦人", "讨厌"]
DEMAND = ["你必须", "你给我", "你说清楚", "你发我", "发我一张", "发张", "发一张", "拍给我",
          "你得", "你要是", "你答应我", "你保证", "你到底"]
COMPLAIN_YOU = ["你又不", "你都没", "你从来", "你每次都", "你不觉得"]

# —— 好评侧（指向对方的正向输出）——
PRAISE = ["好看", "漂亮", "美", "厉害", "棒", "优秀", "可爱", "甜", "温柔", "体贴", "懂事",
          "喜欢你", "爱你", "想你", "真好", "太好了", "不错", "很赞", "点赞", "佩服", "感谢",
          "谢谢你", "辛苦了", "有你在", "开心"]
PRAISE_ROLE = ["你真懂事", "你很懂事", "你真乖", "你很乖", "你真好养", "你真体贴", "你很体贴"]  # PR3 角色化赞美

MEDIA = ["[图片]", "[照片]", "[视频]", "[表情]", "[动画表情]", "[语音]"]

# —— 误报过滤（安全区 / 非指向对方）——
BLAME_SAFE = ["不好意思", "还不错", "你又不认识", "你又不是"]   # 客套、玩笑、非评价
PRAISE_SAFE = ["开心的事情", "要不要", "怎么样", "?"]
# 纯媒体行不算好评（分享≠表扬），单独计为 share
NEG_IN_QUESTION = ["还是", "还是喜欢", "喜欢下雨"]


def hits(t, ws):
    return [w for w in ws if w in t]


def classify(txt):
    """返回 (side, kind, 命中的词) —— side: blame/praise/None"""
    t = txt.strip()
    if not t:
        return None, None, []
    # 排除安全区：自嘲 / 吐槽环境
    if t.startswith("我") and ("笨" in t or "傻" in t):
        return None, "self_deprecate", []
    # 纯媒体行 = share，不算好评
    stripped = t
    for m in MEDIA:
        stripped = stripped.replace(m, "")
    stripped = stripped.strip(" 　")
    if any(m in t for m in MEDIA) and (not stripped or "媒体" in stripped):
        return "share", "media", []

    blame = (hits(t, CRITICIZE) + hits(t, COMPLAIN_YOU))
    dem = hits(t, DEMAND)
    neg = hits(t, NEG_EVAL)
    if blame or dem:
        if any(s in t for s in BLAME_SAFE):
            blame, dem = [], []
        else:
            kind = "criticize" if blame else "demand"
            return "blame", kind, (blame + dem)[:3]
    if neg and ("你" in t):
        if any(s in t for s in BLAME_SAFE):
            pass
        elif any(q in t for q in NEG_IN_QUESTION) and "？" in t or "?" in t or "还是" in t:
            pass                                  # 偏好提问，不是评价
        else:
            return "blame", "neg_eval", neg[:3]

    # 好评必须**指向对方**（含"你"/"咱"/"我们"），否则只是自己的正向情绪
    pr = hits(t, PRAISE)
    if pr and ("你" in t or "咱" in t or "我们" in t):
        body = t.replace("媒体：当前导出格式记录媒体状态，但不复制媒体文件", "")
        for m in MEDIA:
            body = body.replace(m, "")
        if not any(("\u4e00" <= ch <= "\u9fff") for ch in body):
            return "share", "media", []
        return "praise", "role" if hits(t, PRAISE_ROLE) else "praise", pr[:3]
    return None, None, []


def main():
    path = sys.argv[1]
    self_name, other_name = "清峰", "柒月"
    if "--self" in sys.argv:
        self_name = sys.argv[sys.argv.index("--self") + 1]
    if "--other" in sys.argv:
        other_name = sys.argv[sys.argv.index("--other") + 1]

    rows = []
    for line in open(path, encoding="utf-8"):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        raw = m.group("ts").replace("-", "/")
        ts = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S" if raw.count(":") == 2 else "%Y/%m/%d %H:%M")
        rows.append({"ts": ts, "who": m.group("who").strip(), "txt": m.group("txt").strip()})
    rows.sort(key=lambda r: r["ts"])

    blame, praise, recv_neg, recv_pos, share = [], [], [], [], []
    for r in rows:
        side, kind, ws = classify(r["txt"])
        rec = {"time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
               "kind": kind, "hits": ws, "text": r["txt"][:80],
               "len": len(r["txt"])}
        if side == "share":
            share.append(rec)
        elif r["who"] == self_name:
            if side == "blame":
                blame.append(rec)
            elif side == "praise":
                praise.append(rec)
        else:
            if side == "blame":
                recv_neg.append(rec)
            elif side == "praise":
                recv_pos.append(rec)

    # 正负配比（按条数粗算，仅作量级参考）
    n_pos, n_neg = len(praise), len(blame)
    ratio = round(n_pos / n_neg, 2) if n_neg else None

    # 命中统计
    def cnt(lst, k):
        return sum(1 for x in lst if x["kind"] == k)

    out = {"meta": {"self": self_name, "other": other_name,
                    "total_msgs": len(rows),
                    "note": "启发式词典筛选，需人工复核；不含语境判断，仅作候选清单"},
           "self_blame": {"n": len(blame),
                          "criticize": cnt(blame, "criticize"),
                          "neg_eval": cnt(blame, "neg_eval"),
                          "demand": cnt(blame, "demand"),
                          "items": blame},
           "self_praise": {"n": len(praise),
                           "role_praise": cnt(praise, "role"),
                           "media": cnt(praise, "media"),
                           "items": praise},
           "other_neg": {"n": len(recv_neg), "items": recv_neg},
           "other_pos": {"n": len(recv_pos), "items": recv_pos},
           "share_media": {"n": len(share)},
           "pos_neg_ratio": ratio}
    json.dump(out, open("pb.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== 差评/好评分析（逐句）===")
    print(f"总消息 {len(rows)}｜我指向她的负向 {len(blame)} 条（批评 {cnt(blame,'criticize')} / "
          f"负面评价 {cnt(blame,'neg_eval')} / 强要 {cnt(blame,'demand')}）｜我的正向 {len(praise)} 条"
          f"（角色化赞美 {cnt(praise,'role')}）")
    print(f"她指向我的负向 {len(recv_neg)} 条｜她给我的正向 {len(recv_pos)} 条｜正负比 {ratio}")
    print("\n[我指向她的负向表达 · 全部]")
    for b in blame:
        print(f"  {b['time'][:16]} [{b['kind']}] {b['hits']} 「{b['text'][:40]}」")
    print(f"（纯媒体分享 {len(share)} 条，不计入好评）")
    print("\n[我的正向表达（指向她） · 全部]")
    for p in praise:
        print(f"  {p['time'][:16]} [{p['kind']}] {p['hits']} 「{p['text'][:44]}」")
    print("\n[她指向我的负向 · 全部]")
    for b in recv_neg:
        print(f"  {b['time'][:16]} [{b['kind']}] 「{b['text'][:40]}」")
    print("\n已写出 pb.json")


if __name__ == "__main__":
    main()
