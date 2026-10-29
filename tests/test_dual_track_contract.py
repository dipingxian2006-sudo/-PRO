"""Static contract tests for mandatory V2 dual-track outputs."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class DualTrackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.protocol = (ROOT / "references" / "goutou-dual-track-analysis.md").read_text(encoding="utf-8")
        cls.chat_advisor = (ROOT / "references" / "goutou-dual-track-chat-advisor.md").read_text(encoding="utf-8")
        cls.utterance_protocol = (ROOT / "references" / "goutou-utterance-dual-analysis.md").read_text(encoding="utf-8")
        cls.case_review = (ROOT / "references" / "goutou-case-review.md").read_text(encoding="utf-8")
        cls.templates = (ROOT / "references" / "output-templates.md").read_text(encoding="utf-8")

    def test_skill_routes_relationship_strategy_to_dual_track_protocol(self):
        self.assertIn("references/goutou-dual-track-analysis.md", self.skill)
        self.assertIn("结果 A｜关系优势策略", self.skill)
        self.assertIn("结果 B｜客观关系与双向沟通", self.skill)
        self.assertIn("除仅分析人格测评的 ASSESSMENT 外", self.skill)

    def test_protocol_contains_all_fixed_fields(self):
        required = (
            "当前优势与劣势",
            "双方投入及主动权的可观察证据",
            "用户需要维护的利益与边界",
            "推荐行动及停止条件",
            "已确认的事实",
            "双方可能的合理关切",
            "尚无法确定的心理动机",
            "沟通建议及停止条件",
        )
        for field in required:
            with self.subTest(field=field):
                self.assertIn(field, self.protocol)

    def test_every_relationship_mode_template_keeps_both_results(self):
        sections = {
            "FULL": between(self.templates, "## FULL 模式", "## 当日聚焦模式"),
            "FOCUSED": between(self.templates, "## 当日聚焦模式", "## SNAPSHOT 模式"),
            "SNAPSHOT": between(self.templates, "## SNAPSHOT 模式", "## QUICK 模式"),
            "QUICK": between(self.templates, "## QUICK 模式", "## jev 即时判断"),
        }
        for mode, section in sections.items():
            with self.subTest(mode=mode):
                self.assertIn("结果 A｜关系优势策略", section)
                self.assertIn("结果 B｜客观关系与双向沟通", section)

    def test_quick_no_longer_requires_full_metrics_or_three_tier_push(self):
        quick = between(self.templates, "## QUICK 模式", "## jev 即时判断")
        self.assertNotIn("仍先跑 Step 1", quick)
        self.assertNotIn("保守：", quick)
        self.assertNotIn("推进：", quick)
        self.assertIn("A 继续", quick)

    def test_advantage_track_rejects_manipulation(self):
        for forbidden in ("制造嫉妒", "操控依赖", "越过明确拒绝"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, self.protocol)

    def test_comprehensive_advice_cannot_replace_tracks(self):
        self.assertIn("综合建议不能替代任一轨", self.skill)
        self.assertIn("不得替代双轨", self.templates)

    def test_chat_advice_routes_to_two_plans_and_final_decision(self):
        self.assertIn("references/goutou-dual-track-chat-advisor.md", self.skill)
        required = (
            "方案一：关系优势型聊天参谋",
            "方案二：客观共情型聊天参谋",
            "形势与优势判断",
            "聊天策略和互动节奏",
            "双方立场与可能的感受",
            "推荐沟通策略",
            "推荐回复话术",
            "可能的下一步互动分支",
            "最终综合决策",
            "推荐倾向",
        )
        for field in required:
            with self.subTest(field=field):
                self.assertIn(field, self.chat_advisor)

    def test_quick_template_keeps_both_advisors(self):
        quick = between(self.templates, "## QUICK 模式", "## jev 即时判断")
        self.assertIn("方案一｜关系优势型聊天参谋", quick)
        self.assertIn("方案二｜客观共情型聊天参谋", quick)
        self.assertIn("最终综合决策", quick)
        self.assertIn("立即发送话术", quick)

    def test_c_and_e_permissions_apply_to_both_plans(self):
        self.assertIn("`C` 时两个方案", self.chat_advisor)
        self.assertIn("立即发送话术：不生成", self.chat_advisor)
        self.assertIn("换话术继续推动同一要求", self.chat_advisor)

    def test_branches_are_conditional_not_predictions(self):
        self.assertIn("只写条件式预案", self.chat_advisor)
        self.assertIn("不能预测对方一定回应", self.skill)
        self.assertIn("若对方主动追问", self.chat_advisor)

    def test_reply_preserves_user_voice_without_forced_humor(self):
        self.assertIn("用户自然的语言和幽默风格", self.skill)
        self.assertIn("不强行加梗", self.chat_advisor)

    def test_recent_cadence_strategy_is_in_daily_and_summary_outputs(self):
        full = between(self.templates, "## FULL 模式", "## 当日聚焦模式")
        focused = between(self.templates, "## 当日聚焦模式", "## SNAPSHOT 模式")
        snapshot = between(self.templates, "## SNAPSHOT 模式", "## QUICK 模式")
        for mode, section in (("FULL", full), ("FOCUSED", focused), ("SNAPSHOT", snapshot)):
            with self.subTest(mode=mode):
                self.assertIn("轻触—响应—收束", section)
                self.assertIn("1–2 周观察窗", section)
                self.assertIn("好奇", section)

    def test_cadence_does_not_require_daily_contact_or_promise_curiosity(self):
        self.assertIn("不规定每天聊", self.skill)
        self.assertIn("待验证假设", self.skill)
        self.assertIn("不是每天必须执行", self.chat_advisor)
        self.assertIn("不得写成“坚持两周她就会好奇”", self.chat_advisor)

    def test_cadence_has_evidence_and_early_stop_conditions(self):
        for marker in ("主动发起", "追问", "回访旧话题", "提出下一步"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.chat_advisor)
        self.assertIn("提前停止或调整", self.chat_advisor)
        self.assertIn("适当间隔是在保护节奏", self.case_review)

    def test_every_relationship_mode_has_utterance_level_dual_analysis(self):
        sections = {
            "FULL": between(self.templates, "## FULL 模式", "## 当日聚焦模式"),
            "FOCUSED": between(self.templates, "## 当日聚焦模式", "## SNAPSHOT 模式"),
            "SNAPSHOT": between(self.templates, "## SNAPSHOT 模式", "## QUICK 模式"),
            "QUICK": between(self.templates, "## QUICK 模式", "## jev 即时判断"),
        }
        for mode, section in sections.items():
            with self.subTest(mode=mode):
                self.assertIn("逐句双轨", section)
                self.assertIn("A 轨｜关系优势", section)
                self.assertIn("B 轨｜客观共情", section)

    def test_utterance_protocol_requires_complete_coverage(self):
        self.assertIn("已完成双轨数应等于分析单元数", self.utterance_protocol)
        self.assertIn("不可读内容仍占一个单元", self.utterance_protocol)
        self.assertIn("不得使用 Top-N", self.utterance_protocol)
        self.assertIn("未观察到优势/主动权信号", self.skill)

    def test_aggregates_must_reference_utterance_units(self):
        self.assertIn("汇总结论必须能回指逐句单元", self.skill)
        self.assertIn("引用一个或多个逐句单元 ID", self.utterance_protocol)
        self.assertIn("完整逐句双轨表作为附录", self.utterance_protocol)


if __name__ == "__main__":
    unittest.main(verbosity=2)
