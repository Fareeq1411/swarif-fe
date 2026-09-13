import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
from agent import Agent, compact_user_jobs_for_llm


class RecordingClient:
    def __init__(self, content='{"ok": true}'):
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )
        self.content = content

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class LlmProviderTests(unittest.TestCase):
    def test_connection_settings_are_saved_to_env_and_removed_from_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            session_path = root / "sessions.json"
            session_path.write_text(json.dumps({
                "id": "user-1",
                "user_id": "user-1",
                "agent_ip": "192.168.1.10",
                "server_port": 9000,
                "agent_is_local": False,
            }), encoding="utf-8")
            environment = {
                "SWARIF_AGENT_IP": "",
                "SWARIF_AGENT_PORT": "8767",
                "SWARIF_AGENT_IS_LOCAL": "false",
            }
            with (
                patch.object(agent, "ENV_PATH", env_path),
                patch.dict(os.environ, environment),
            ):
                saved = Agent.save_connection_settings(
                    "127.0.0.1", 8767, True, session_path
                )

            persisted = env_path.read_text(encoding="utf-8")
            session = json.loads(session_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["agent_ip"], "127.0.0.1")
        self.assertIn("SWARIF_AGENT_IP=127.0.0.1", persisted)
        self.assertIn("SWARIF_AGENT_IS_LOCAL=true", persisted)
        self.assertNotIn("agent_ip", session)
        self.assertNotIn("server_port", session)
        self.assertNotIn("agent_is_local", session)

    def test_list_user_files_returns_exact_recursive_workspace_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "Client Reports"
            reports.mkdir()
            (reports / "Q3 final.xlsx").write_text("data", encoding="utf-8")
            (root / "notes.txt").write_text("notes", encoding="utf-8")

            with patch.object(Agent, "user_files_path", return_value=root):
                result = Agent.list_user_files()

        paths = [entry["path"] for entry in result["entries"]]
        self.assertEqual(
            paths,
            ["Client Reports", "notes.txt", "Client Reports/Q3 final.xlsx"],
        )
        self.assertNotIn(str(root), paths)

    def test_fetch_user_jobs_only_returns_current_users_pending_jobs(self):
        fetched_jobs = [
            {"id": "pending", "user_id": "user-1", "status": 0},
            {"id": "processing", "user_id": "user-1", "status": 3},
            {"id": "completed", "user_id": "user-1", "status": 1},
            {"id": "other-user", "user_id": "user-2", "status": 0},
        ]
        with (
            patch.object(
                Agent,
                "read_session",
                return_value={"id": "user-1", "user_id": "user-1"},
            ),
            patch.object(Agent, "fetch_jobs", return_value=fetched_jobs) as fetch,
        ):
            result = Agent.fetch_user_jobs()

        fetch.assert_called_once_with("active", limit=50)
        self.assertEqual(
            [job["id"] for job in result["pending_jobs"]],
            ["pending", "processing"],
        )

    def test_job_tool_data_is_compacted_before_llm_use(self):
        huge_value = "x" * 500_000
        compacted = compact_user_jobs_for_llm({
            "user_id": "user-1",
            "pending_jobs": [{
                "id": "job-1",
                "status": "3",
                "completion_feedback": huge_value,
                "planner_data": huge_value,
            }],
            "visibility_note": "recent jobs may be hidden",
        })

        job = compacted["pending_jobs"][0]
        self.assertNotIn("planner_data", job)
        self.assertEqual(len(job["completion_feedback"]), 2000)
        self.assertLess(len(json.dumps(compacted)), 3000)

    def test_gemini_uses_gemini_model(self):
        client = RecordingClient()
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-test"}):
            result = Agent.generate_ai_content(
                "hello", "return data", provider="gemini", client=client
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.calls[0]["model"], "gemini-test")

    def test_openrouter_uses_openrouter_model(self):
        client = RecordingClient()
        with patch.dict(os.environ, {"OPENROUTER_MODEL": "vendor/model"}):
            Agent.generate_ai_content(
                "hello", "return data", provider="openrouter", client=client
            )

        self.assertEqual(client.calls[0]["model"], "vendor/model")
        self.assertEqual(
            client.calls[0]["extra_body"],
            {"reasoning": {"effort": "low"}},
        )

    def test_reasoning_setting_is_only_sent_to_openrouter(self):
        client = RecordingClient()
        Agent.generate_ai_content(
            "hello", "return data", provider="gemini", client=client
        )

        self.assertNotIn("extra_body", client.calls[0])

    def test_extracts_json_object_from_markdown_and_extra_text(self):
        client = RecordingClient(
            "Here is the result:\n```json\n"
            '{"next_action":"send_message","args":{"message":"Done {safely}"}}'
            "\n```\nThis is additional text."
        )

        result = Agent.generate_ai_content(
            "hello", "return data", provider="openrouter", client=client
        )

        self.assertEqual(result["next_action"], "send_message")
        self.assertEqual(result["args"]["message"], "Done {safely}")

    def test_skips_non_json_braces_before_valid_object(self):
        client = RecordingClient('Explanation {not JSON}. Result: {"ok": true} trailing')

        result = Agent.generate_ai_content(
            "hello", "return data", provider="gemini", client=client
        )

        self.assertEqual(result, {"ok": True})

    def test_rejects_response_without_any_json_object(self):
        client = RecordingClient("There is no JSON in this response.")

        with self.assertRaisesRegex(RuntimeError, "does not contain a valid JSON object"):
            Agent.generate_ai_content(
                "hello", "return data", provider="deepseek", client=client
            )

    def test_provider_can_be_selected_from_environment(self):
        client = RecordingClient()
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-from-env"},
        ):
            Agent.generate_ai_content("hello", "return data", client=client)

        self.assertEqual(client.calls[0]["model"], "gemini-from-env")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported LLM provider"):
            Agent.generate_ai_content(
                "hello", "return data", provider="unknown", client=RecordingClient()
            )

    def test_llm_call_is_written_to_agent_log(self):
        client = RecordingClient()
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "agent_log.txt"
            with patch.object(agent, "AGENT_LOG_PATH", log_path):
                Agent.generate_ai_content(
                    "hello",
                    "return data",
                    provider="gemini",
                    model="gemini-test",
                    step="decide_action",
                    client=client,
                )
            record = json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(record["provider"], "gemini")
        self.assertEqual(record["model"], "gemini-test")
        self.assertEqual(record["step"], "decide_action")
        self.assertEqual(record["status"], "completed")
        self.assertIn("duration_ms", record)
        self.assertNotIn("llm_response", record)


if __name__ == "__main__":
    unittest.main()
