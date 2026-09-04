import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication

from agent import Agent
from app import SwarifBackend
from swarif_ui import Composer


class FakeWindow(QObject):
    message_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()
    learning_mode_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.learning_mode = False
        self.current_page = None
        self.progress = []

    def update_job_progress(self, job_id, progress):
        self.progress.append((job_id, progress))

    def set_task_state(self, _state):
        pass

    def set_agent_typing(self, _typing, _summary):
        pass

    def update_jobs(self, _active, _inactive):
        pass


class FakeConnection:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    def cancel_job(self, user_id, job_id):
        self.calls.append((user_id, job_id))
        return self.accepted


class TaskLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_composer_keeps_stop_for_thinking_and_executing(self):
        composer = Composer()
        composer.set_task_state("thinking")
        self.assertEqual(composer._task_state, "thinking")
        self.assertEqual(composer.send.toolTip(), "Stop")
        self.assertEqual(composer.send.text(), "")
        self.assertTrue(composer.stop_status.isHidden())
        composer.set_task_state("executing")
        self.assertEqual(composer._task_state, "executing")
        self.assertEqual(composer.send.toolTip(), "Stop")
        self.assertEqual(composer.send.text(), "")
        composer.set_task_state("stopping")
        self.assertEqual(composer.send.text(), "")
        self.assertEqual(composer.stop_status.text(), "Stopping…")
        self.assertFalse(composer.stop_status.isHidden())
        composer.set_task_state("idle")
        self.assertEqual(composer.send.toolTip(), "Send (Shift+Enter)")
        self.assertTrue(composer.stop_status.isHidden())

    def test_stop_cancels_only_exact_submitted_job(self):
        window = FakeWindow()
        backend = SwarifBackend(window)
        backend._append_status_message = lambda *_args: None
        connection = FakeConnection()
        backend.set_server_connection(connection)
        task = {
            "state": "executing",
            "cancel_event": threading.Event(),
            "active_job_id": "job-123",
            "org_id": "org-1",
            "user_id": "user-1",
            "cancel_requested": False,
        }
        backend._active_task = task

        backend.stop_current_task()
        deadline = time.time() + 1
        while backend._active_task is not None and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(connection.calls, [("user-1", "job-123")])
        self.assertIsNone(backend._active_task)
        self.assertIn("job-123", backend._cancelled_job_ids)
        time.sleep(0.05)

    def test_complete_state_sequence_after_submission(self):
        window = FakeWindow()
        backend = SwarifBackend(window)
        backend._append_status_message = lambda *_args: None
        connection = FakeConnection()
        backend.set_server_connection(connection)
        states = []
        backend.task_state_changed.connect(states.append, Qt.DirectConnection)
        task = {
            "state": "thinking",
            "cancel_event": threading.Event(),
            "active_job_id": None,
            "org_id": "org-1",
            "user_id": "user-1",
            "cancel_requested": False,
        }
        backend._active_task = task

        backend._emit_task_state("thinking")
        task["active_job_id"] = "job-456"
        task["state"] = "executing"
        backend._emit_task_state("executing")
        backend.stop_current_task()
        deadline = time.time() + 1
        while len(states) < 4 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(
            states,
            ["thinking", "executing", "stopping", "idle"],
        )
        self.assertEqual(connection.calls, [("user-1", "job-456")])

    def test_late_progress_and_completion_for_cancelled_job_are_ignored(self):
        window = FakeWindow()
        backend = SwarifBackend(window)
        backend._cancelled_job_ids.add("job-cancelled")

        backend.receive_job_progress("job-cancelled", "late progress")
        backend.receive_job_completion("job-cancelled", {"status": 1})

        self.assertEqual(window.progress, [])
        self.assertIsNone(backend._active_task)

    def test_restart_recovers_one_processing_job_and_stops_only_it(self):
        window = FakeWindow()
        backend = SwarifBackend(window)
        backend._append_status_message = lambda *_args: None
        connection = FakeConnection()
        backend.set_server_connection(connection)

        recovered = backend._recover_active_job([
            {"id": "job-newest", "status": 3},
            {"id": "job-older", "status": 3},
        ])
        self.assertEqual(recovered, "job-newest")
        self.assertEqual(backend._active_task["active_job_id"], "job-newest")
        self.assertEqual(backend._active_task["state"], "executing")
        recovered_user_id = backend._active_task["user_id"]

        backend.stop_current_task()
        deadline = time.time() + 1
        while backend._active_task is not None and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        self.assertEqual(connection.calls, [(recovered_user_id, "job-newest")])

    def test_failed_cancellation_keeps_job_active_and_reports_failure(self):
        window = FakeWindow()
        backend = SwarifBackend(window)
        notices = []
        backend._append_status_message = lambda message, *_args: notices.append(message)
        connection = FakeConnection(accepted=False)
        backend.set_server_connection(connection)
        task = {
            "state": "executing",
            "cancel_event": threading.Event(),
            "active_job_id": "job-live",
            "org_id": "org-1",
            "user_id": "user-1",
            "cancel_requested": False,
        }
        backend._active_task = task

        backend.stop_current_task()
        deadline = time.time() + 1
        while task.get("cancel_pending") and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(task["state"], "executing")
        self.assertEqual(task["active_job_id"], "job-live")
        self.assertEqual(
            notices,
            ["Cancellation failed: the server did not confirm cancellation"],
        )
        self.assertNotIn("job-live", backend._cancelled_job_ids)

    def test_decide_action_reports_created_job_through_callback(self):
        created_job = {"id": "job-created"}
        reported_jobs = []
        responses = [
            {
                "next_action": "submit_job",
                "args": {
                    "task_title": "Test task",
                    "task_prompt": {"instructions": "Do the test"},
                    "extra_data": {},
                },
                "step_summary": "Submitted",
            },
            {
                "next_action": "send_message",
                "args": {"message": "The task was submitted."},
            },
        ]

        with (
            patch.object(Agent, "reset_context_memory"),
            patch.object(Agent, "fetch_chat", return_value=[]),
            patch.object(Agent, "read_memory", return_value=""),
            patch.object(Agent, "get_context_memory", return_value={}),
            patch.object(Agent, "extra_data", return_value={}),
            patch.object(Agent, "read_behavior", return_value=""),
            patch.object(Agent, "generate_llm", side_effect=responses),
            patch.object(Agent, "submit_job", return_value=created_job),
            patch.object(Agent, "update_context_memory"),
            patch.object(Agent, "reply_message", return_value=True),
        ):
            Agent.decide_action(
                "Run a test task",
                "org-1",
                "user-1",
                on_job_created=reported_jobs.append,
            )

        self.assertEqual(reported_jobs, [created_job])

    def test_decide_action_retries_execution_refusal(self):
        responses = [
            {
                "next_action": "send_message",
                "args": {"message": "I'm not able to perform that operation."},
            },
            {
                "next_action": "send_message",
                "args": {
                    "message": (
                        "I will submit the requested operation as a job. It has not "
                        "been submitted yet. Please confirm."
                    )
                },
            },
        ]

        with (
            patch.object(Agent, "reset_context_memory"),
            patch.object(Agent, "fetch_chat", return_value=[]),
            patch.object(Agent, "read_memory", return_value=""),
            patch.object(Agent, "get_context_memory", return_value={}),
            patch.object(Agent, "extra_data", return_value={}),
            patch.object(Agent, "read_behavior", return_value=""),
            patch.object(Agent, "generate_llm", side_effect=responses) as generate,
            patch.object(Agent, "reply_message", return_value=True) as reply,
        ):
            Agent.decide_action(
                "Perform the requested external operation",
                "org-1",
                "user-1",
            )

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(
            generate.call_args.kwargs["step"], "decide_action_role_correction"
        )
        self.assertNotIn("not able", reply.call_args.args[0].casefold())


if __name__ == "__main__":
    unittest.main()
