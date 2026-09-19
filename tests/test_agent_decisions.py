import unittest
from unittest.mock import patch

from agent import Agent


class AgentDecisionTests(unittest.TestCase):
    policy = {"action_avalailble": ["send_message", "submit_job"], "reply_format": {}}

    def test_empty_decision_retries_and_recovers(self):
        valid = {"next_action": "send_message", "args": {"message": "Here is my reply"}}
        with patch.object(Agent, "generate_llm", return_value=valid) as generate:
            result = Agent._ensure_decision(None, {}, self.policy, "decision")
        self.assertEqual(result, valid)
        generate.assert_called_once()

    def test_invalid_decisions_always_produce_nonempty_fallback_reply(self):
        for invalid in (None, {}, {"next_action": "unknown", "args": {}},
                        {"next_action": "send_message", "args": {"message": "   "}},
                        {"next_action": "submit_job", "args": {}}):
            with self.subTest(invalid=invalid):
                with patch.object(Agent, "generate_llm", return_value=invalid) as generate, \
                     patch.object(Agent, "submit_job") as submit:
                    result = Agent._ensure_decision(invalid, {}, self.policy, "decision")
                self.assertEqual(generate.call_count, 2)
                self.assertEqual(result["next_action"], "send_message")
                self.assertTrue(result["args"]["message"].strip())
                submit.assert_not_called()

    def test_learning_mode_fallback_uses_learning_reply_action(self):
        policy = {"action_avalailble": ["reply_message", "generator_job"]}
        with patch.object(Agent, "generate_llm", return_value={}):
            result = Agent._ensure_decision({}, {}, policy, "learning")
        self.assertEqual(result["next_action"], "reply_message")

    def test_cancelled_decision_does_not_retry(self):
        with patch.object(Agent, "generate_llm") as generate:
            result = Agent._ensure_decision(None, {}, self.policy, "decision", lambda: False)
        self.assertIsNone(result)
        generate.assert_not_called()
