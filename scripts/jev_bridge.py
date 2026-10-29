# -*- coding: utf-8 -*-
"""relationship-intelligence × jev-chat 胶水层（micro 增强）。

职责：把 macro 层（relationship-intelligence）的产物喂给 jev-chat 的 analyze，
再把结果回贴成 macro 友好的结构，供 SKILL.md 的 Step 4.5 / QUICK 使用。

设计约束（用户 9/26 备注 · 重点：悬浮窗不要）：
- 本层只处理用户主动导入的材料（文本 / 文件 / JSON）；**绝不接入微信侧边栏、悬浮窗、
  实时 OCR、任何常驻监听 / 自动抓取**。jev-chat 改造版已是「用户提交材料 → 一次性分析」，符合。
- jev 的 ingest.py / analyze.py / questions.py 作为依赖引用（import），**不复制逻辑**，保持单一真相源。
- 联网依赖（analyze.py 自带，纯 urllib，无第三方 pip 依赖）：
      JEV_API_KEY  → OpenRouter / TypeSafe（判断 + 排序）
      LLM_API_KEY  → DeepSeek（起草候选）
- 任何情况下不写 key 到文件 / 日志；调用失败时只在结果里标 reason，不崩。

降级逻辑（jev 依赖 API）：
- 未设 key / 模块加载失败 / 调用抛错 → 返回 {"enabled": false, "reason": ...}，
  上层据此走纯离线三档话术，不中断主流程。
- --dry-run 不联网，用于离线自检 ingest + 调用链路（跳过 key 预检）。

调用示例：
    python jev_bridge.py 片段.txt --me 清峰 --reply-to 柒月 --relationship 恋人 \
        --relationship-custom "高焦虑直球型，安全感接口=有回应/被记得；禁区=被小看/冷处理" \
        -o jev_out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# jev-chat 脚本目录（用户级 skill 标准位置）；可用环境变量 JEV_SCRIPTS_DIR 覆盖。
JEV_SKILL_SCRIPTS = os.environ.get("JEV_SCRIPTS_DIR") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "skills", "jev-chat", "scripts")

# choice 值 → 中文呈现（呈现层标签，源自 jev-chat SKILL.md 第 4/5 节；不复制判据逻辑）
CHOICE_LABELS = {
    "true_intent": {
        "confirm_you_care": "想确认你在意",
        "vent_frustration": "发泄不满",
        "want_action": "要你行动",
        "want_explanation": "要你解释",
        "casual_chat": "轻松闲聊",
        "peaceful_end": "平和结束",
    },
    "best_action": {
        "check_records": "先核对记录",
        "apologize": "道歉",
        "give_commitment": "给承诺",
        "explain": "解释",
        "acknowledge": "回应并表达理解",
        "say_less": "少说",
        "make_arrangement": "排安排",
    },
    "partner_needs": {
        "apology": "对方要的是道歉",
        "action": "对方要的是行动",
        "explanation": "对方要的是解释",
        "care": "对方要的是在意",
        "nothing": "对方其实什么都不要了",
    },
}


def _label(question: str, value) -> str:
    if value is None:
        return "未知"
    return CHOICE_LABELS.get(question, {}).get(value, str(value))


def _load_jev():
    """注入 jev scripts 目录并 import ingest / analyze。失败抛 ImportError。"""
    if not os.path.isdir(JEV_SKILL_SCRIPTS):
        raise ImportError(f"jev-chat 脚本目录不存在：{JEV_SKILL_SCRIPTS}")
    if JEV_SKILL_SCRIPTS not in sys.path:
        sys.path.insert(0, JEV_SKILL_SCRIPTS)
    import analyze  # noqa: F401
    import ingest   # noqa: F401
    return analyze, ingest


def _confidence_note(result: dict) -> str:
    if result.get("dry_run"):
        return "dry_run 未联网，未产生判断与候选"
    scored = [s for s in result.get("scores", []) if s]
    if not scored:
        return "本次无判断依据，候选为猜（judged=false）"
    best = max(scored)
    if best >= 0.9:
        return "判断把握高，可按 best_action 直走"
    if best >= 0.7:
        return "结论可用，但需附带替代解释"
    if best >= 0.5:
        return "把握不大，只当线索"
    return "相当于没答，改用直接读对话"


def run(source: str, me: str, reply_to: str | None = None,
        relationship: str = "恋人", relationship_custom: str = "",
        context: int = 10, style: str = "", dry_run: bool = False,
        output: str | None = None) -> dict:
    """跑 jev micro 增强；任何失败都降级，不抛。"""

    # 降级检查 1：dry_run 不联网，跳过 key 预检；其余必须两把 key 都在
    if not dry_run:
        need_jev = os.environ.get("JEV_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        need_llm = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not need_jev:
            return {"enabled": False, "reason": "JEV_API_KEY 未设置，走离线三档话术",
                    "candidates": [], "answers": {}, "judged": False}
        if not need_llm:
            return {"enabled": False, "reason": "LLM_API_KEY 未设置，走离线三档话术",
                    "candidates": [], "answers": {}, "judged": False}

    try:
        analyze, ingest = _load_jev()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "reason": f"无法加载 jev 模块：{exc}",
                "candidates": [], "answers": {}, "judged": False}

    try:
        messages = ingest.load_any(ingest.read_text(source), me=me, kind="auto")
        result = analyze.analyze(
            messages,
            relationship=relationship_custom or relationship,
            keep=context, style=style, reply_to=reply_to,
            timeout=30, dry_run=dry_run)
    except analyze.JevError as exc:
        return {"enabled": False, "reason": f"jev 调用失败：{exc}",
                "candidates": [], "answers": {}, "judged": False}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "reason": f"处理失败：{exc}",
                "candidates": [], "answers": {}, "judged": False}

    ans = result.get("answers", {})

    def choice(q: str) -> dict:
        v = ans.get(q) or {}
        return {"value": v.get("choice"), "label": _label(q, v.get("choice")),
                "confidence": v.get("confidence")}

    danger = (ans.get("danger_level") or {}).get("score")
    danger_conf = (ans.get("danger_level") or {}).get("confidence")

    ranked = [{"reply": it.get("reply"), "score": it.get("score")}
              for it in result.get("ranked", [])]

    out = {
        "enabled": True,
        "judged": result.get("judged", False),
        "judge_error": result.get("judge_error"),
        "intent": choice("true_intent"),
        "literal_question": (ans.get("literal_question") or {}).get("noul"),
        "danger_level": {"score": danger, "confidence": danger_conf},
        "should_reply_now": (ans.get("should_reply_now") or {}).get("noul"),
        "best_action": choice("best_action"),
        "partner_needs": choice("partner_needs"),
        "tension_resolved": (ans.get("tension_resolved") or {}).get("noul"),
        "candidates": ranked,
        "best_reply": result.get("best_reply"),
        "confidence_note": _confidence_note(result),
        "meta": result.get("meta", {}),
    }
    if dry_run:
        out["dry_run"] = True
        out["state"] = result.get("state")
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="relationship-intelligence × jev 胶水层（micro 增强）")
    p.add_argument("source", help="聊天片段文件路径，或直接粘贴的内容")
    p.add_argument("--me", required=True, help="本人名字（谁在发消息）")
    p.add_argument("--reply-to", help="对方名字（回复对象；群聊时指定）")
    p.add_argument("--relationship", default="恋人",
                   choices=["恋人", "朋友", "同事", "家人", "客户", "供应商", "自定义"])
    p.add_argument("--relationship-custom", default="",
                   help="画像护栏摘要（来自 Step 2 画像卡：禁区 / 需求触发点 / 喜欢-抗拒的互动）")
    p.add_argument("--context", type=int, default=10, help="看最近几条，默认 10")
    p.add_argument("--style", default="", help="一句话描述本人口吻")
    p.add_argument("--dry-run", action="store_true", help="不联网，打印将发送给 Jev 的请求体")
    p.add_argument("-o", "--output", help="写出 JSON 的路径；不给就打 stdout")
    args = p.parse_args()

    out = run(args.source, args.me, args.reply_to, args.relationship,
              args.relationship_custom, args.context, args.style,
              args.dry_run, args.output)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
