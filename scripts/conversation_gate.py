"""Deterministic V2 A-E gate for already verified, structured observations.

This module does not infer intent from natural language. The caller must first
separate quotes, sent messages, drafts, user accounts, history, and inference.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


class GateInputError(ValueError):
    pass


@dataclass(frozen=True)
class Observation:
    other_asked_or_extended: bool = False
    other_left_reply_hook: bool = False
    polite_ack: bool = False
    explicit_closing: bool = False
    explicit_refusal: bool = False
    conflict_or_discomfort: bool = False
    no_reply: bool = False
    pending_shared_matter: bool = False
    independent_new_topic_ready: bool = False
    last_user_text_is_unsent_draft: bool = False


DECISIONS = {
    "A": {"label": "继续", "reply_permission": "normal"},
    "B": {"label": "轻接收束", "reply_permission": "brief_only"},
    "C": {"label": "不回复", "reply_permission": "none"},
    "D": {"label": "另起话题", "reply_permission": "new_topic_only"},
    "E": {"label": "修复降压", "reply_permission": "repair_only"},
}


def _observation(value: Observation | Mapping[str, Any]) -> Observation:
    if isinstance(value, Observation):
        return value
    if not isinstance(value, Mapping):
        raise GateInputError("observation must be an Observation or mapping")
    known = set(Observation.__dataclass_fields__)
    extra = set(value) - known
    if extra:
        raise GateInputError("unknown observation fields: " + ", ".join(sorted(extra)))
    invalid = [key for key, item in value.items() if not isinstance(item, bool)]
    if invalid:
        raise GateInputError("observation fields must be boolean: " + ", ".join(sorted(invalid)))
    return Observation(**value)


def decide(value: Observation | Mapping[str, Any]) -> dict[str, Any]:
    """Apply boundary-first priority and return a qualitative decision record."""
    obs = _observation(value)

    if obs.explicit_refusal or obs.conflict_or_discomfort:
        code = "E"
        reason = "存在明确拒绝、不适或冲突；先停止推动并修复降压。"
    elif obs.other_asked_or_extended or obs.other_left_reply_hook:
        code = "A"
        reason = "对方主动扩展或留下了明确的回应入口。"
    elif obs.explicit_closing or obs.no_reply:
        code = "C"
        reason = "对方已经收尾或尚无新回复，当前没有理由追加消息。"
    elif obs.pending_shared_matter or obs.independent_new_topic_ready:
        code = "D"
        reason = "存在独立的新内容或需要另行处理的共同事项。"
    elif obs.polite_ack:
        code = "B"
        reason = "对方有礼貌回应但没有新入口，只适合简短承接。"
    else:
        code = "C"
        reason = "没有观察到问题、待办或自然接话入口。"

    active = "存在" if obs.other_asked_or_extended else "缺少"
    extension = "存在" if obs.other_left_reply_hook else "缺少"
    if obs.explicit_refusal:
        tone = "明确拒绝"
    elif obs.conflict_or_discomfort:
        tone = "不适或冲突"
    elif obs.polite_ack:
        tone = "礼貌回应"
    else:
        tone = "不明确"
    boundary = "已表达边界" if obs.explicit_refusal else "未观察到明确边界"
    if obs.pending_shared_matter:
        context = "有共同事项"
    elif obs.independent_new_topic_ready:
        context = "有独立新内容"
    elif obs.no_reply:
        context = "暂无回复"
    elif obs.explicit_closing:
        context = "话题已收尾"
    else:
        context = "无额外相邻情境"

    rule = DECISIONS[code]
    return {
        "code": code,
        "label": rule["label"],
        "reply_permission": rule["reply_permission"],
        "should_generate_reply": rule["reply_permission"] != "none",
        "reason": reason,
        "indicators": {
            "active_expansion": active,
            "topic_extension": extension,
            "emotion_tone": tone,
            "boundary_autonomy": boundary,
            "adjacent_context": context,
        },
        "source_state": {
            "last_user_text_is_unsent_draft": obs.last_user_text_is_unsent_draft,
            "draft_counted_as_sent": False,
        },
        "observation": asdict(obs),
    }


ISSUE_FIELDS = {
    "personal_autonomy": "个人自主",
    "shared_coordination": "共同协商",
    "shared_commitment": "共同承诺",
    "uncontrollable_outcome": "不可控结果",
}


def classify_issue(value: Mapping[str, Any]) -> str:
    """Classify explicit issue properties; never infer them from prose."""
    if not isinstance(value, Mapping):
        raise GateInputError("issue properties must be a mapping")
    extra = set(value) - set(ISSUE_FIELDS)
    if extra:
        raise GateInputError("unknown issue fields: " + ", ".join(sorted(extra)))
    invalid = [key for key, item in value.items() if not isinstance(item, bool)]
    if invalid:
        raise GateInputError("issue fields must be boolean: " + ", ".join(sorted(invalid)))
    selected = [ISSUE_FIELDS[key] for key, enabled in value.items() if enabled]
    if len(selected) > 1:
        return "混合议题"
    return selected[0] if selected else "未分类"
