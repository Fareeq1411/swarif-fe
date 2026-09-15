import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
from agent import Agent, compact_user_jobs_for_llm


class JsonResponse(BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class RecordingClient:
    def __init__(self, content='{"ok": true}'):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.content = content

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(
                prompt_tokens=3, completion_tokens=2, total_tokens=5
            ),
        )


class LlmProviderTests(unittest.TestCase):
    def test_connection_settings_are_saved_as_config_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "frontend_config.json"
            session_path = root / "sessions.json"
            session_path.write_text(json.dumps({
                "id": "user-1",
                "user_id": "user-1",
                "agent_ip": "192.168.1.10",
                "server_port": 9000,
                "agent_is_local": False,
            }), encoding="utf-8")
            with patch.object(agent, "FRONTEND_CONFIG_PATH", config_path):
                saved = Agent.save_connection_settings(
                    "127.0.0.1", 8767, True, session_path
                )

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            session = json.loads(session_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["agent_ip"], "127.0.0.1")
        self.assertEqual(persisted["overrides"]["SWARIF_AGENT_IP"], "127.0.0.1")
        self.assertTrue(persisted["overrides"]["SWARIF_AGENT_IS_LOCAL"])
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

    def test_frontend_config_is_fetched_cached_and_merged_with_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "frontend_config.json"
            config_path.write_text(json.dumps({
                "remote": {},
                "overrides": {"SWARIF_AGENT_IP": "192.168.1.50"},
            }), encoding="utf-8")
            response = JsonResponse({
                "LLM_PROVIDER": "deepseek",
                "SWARIF_AGENT_IP": "127.0.0.1",
                "SWARIF_AGENT_IS_LOCAL": False,
            })
            with (
                patch.object(agent, "FRONTEND_CONFIG_PATH", config_path),
                patch.object(agent, "urlopen", return_value=response) as opened,
            ):
                result = Agent.refresh_frontend_config()
                cached = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result["SWARIF_AGENT_IP"], "192.168.1.50")
        self.assertEqual(cached["remote"]["LLM_PROVIDER"], "deepseek")
        self.assertEqual(opened.call_args.args[0].get_method(), "GET")

    def test_llm_api_key_is_fetched_for_logged_in_user_and_cached_in_memory(self):
        response = JsonResponse({"llm_api_key": "organization-key"})
        session = {"token": "jwt-token", "user_id": "user-1", "type": "user"}
        Agent.clear_llm_api_key_cache()
        with (
            patch.object(Agent, "read_session", return_value=session),
            patch.object(agent, "urlopen", return_value=response) as opened,
        ):
            first = Agent.get_llm_api_key()
            second = Agent.get_llm_api_key()

        request = opened.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.swarif.com/api/swarif/get-llm-api-key")
        self.assertEqual(body, {"id": "user-1", "token": "jwt-token", "type": "user"})
        self.assertEqual(first, "organization-key")
        self.assertEqual(second, "organization-key")
        self.assertEqual(opened.call_count, 1)

    def test_llm_api_key_requires_session(self):
        with patch.object(Agent, "read_session", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "session has expired"):
                Agent.get_llm_api_key(force_refresh=True)

    def test_generate_ai_content_runs_locally_with_fetched_config_and_key(self):
        client = RecordingClient('{"next_action":"send_message","args":{"message":"Done"}}')
        config = {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.test",
            "DEEPSEEK_MODEL": "deepseek-test",
        }
        with (
            patch.object(Agent, "frontend_config", return_value=config),
            patch.object(Agent, "get_llm_api_key", return_value="organization-key"),
            patch.object(agent, "OpenAI", return_value=client) as openai_client,
        ):
            result = Agent.generate_ai_content(
                {"request": "hello"}, {"policy": "return data"}, step="decide_action"
            )

        openai_client.assert_called_once_with(
            api_key="organization-key",
            base_url="https://api.deepseek.test",
            timeout=60,
            default_headers=None,
        )
        self.assertEqual(client.calls[0]["model"], "deepseek-test")
        self.assertEqual(client.calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(result["args"]["message"], "Done")

    def test_llm_call_is_written_to_agent_log(self):
        client = RecordingClient()
        config = {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.test",
            "DEEPSEEK_MODEL": "deepseek-test",
        }
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "agent_log.txt"
            with (
                patch.object(agent, "AGENT_LOG_PATH", log_path),
                patch.object(Agent, "frontend_config", return_value=config),
                patch.object(Agent, "get_llm_api_key", return_value="key"),
                patch.object(agent, "OpenAI", return_value=client),
            ):
                Agent.generate_ai_content(
                    "hello",
                    "return data",
                    step="decide_action",
                )
            record = json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(record["provider"], "deepseek")
        self.assertEqual(record["model"], "deepseek-test")
        self.assertEqual(record["step"], "decide_action")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["total_tokens"], 5)
        self.assertIn("duration_ms", record)
        self.assertNotIn("llm_response", record)


if __name__ == "__main__":
    unittest.main()
