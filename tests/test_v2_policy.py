"""Behavioral acceptance tests for the V2 decision gate."""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from conversation_gate import GateInputError, classify_issue, decide


class DecisionGateTests(unittest.TestCase):
    def test_other_active_question_allows_normal_reply(self):
        result = decide({"other_asked_or_extended": True})
        self.assertEqual(result["code"], "A")
        self.assertEqual(result["reply_permission"], "normal")

    def test_explicit_closing_stops_instead_of_extending(self):
        result = decide({"explicit_closing": True, "polite_ack": True})
        self.assertEqual(result["code"], "C")
        self.assertFalse(result["should_generate_reply"])

    def test_unsent_draft_is_not_conversation_fact(self):
        result = decide({"last_user_text_is_unsent_draft": True})
        self.assertEqual(result["code"], "C")
        self.assertFalse(result["source_state"]["draft_counted_as_sent"])

    def test_refusal_overrides_reply_hook_and_new_topic(self):
        result = decide({
            "explicit_refusal": True,
            "other_left_reply_hook": True,
            "independent_new_topic_ready": True,
        })
        self.assertEqual(result["code"], "E")
        self.assertEqual(result["reply_permission"], "repair_only")

    def test_no_reply_does_not_become_new_topic(self):
        result = decide({"no_reply": True, "independent_new_topic_ready": True})
        self.assertEqual(result["code"], "C")
        self.assertFalse(result["should_generate_reply"])

    def test_polite_ack_permits_only_brief_closure(self):
        result = decide({"polite_ack": True})
        self.assertEqual(result["code"], "B")
        self.assertEqual(result["reply_permission"], "brief_only")

    def test_shared_matter_routes_to_separate_topic(self):
        result = decide({"pending_shared_matter": True})
        self.assertEqual(result["code"], "D")
        self.assertEqual(result["reply_permission"], "new_topic_only")

    def test_personal_choice_and_shared_effect_is_mixed(self):
        issue = classify_issue({"personal_autonomy": True, "shared_coordination": True})
        self.assertEqual(issue, "混合议题")

    def test_unknown_fields_fail_closed(self):
        with self.assertRaises(GateInputError):
            decide({"mind_reading_score": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
