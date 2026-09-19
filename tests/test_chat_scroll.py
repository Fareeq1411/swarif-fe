import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from swarif_ui import ChatPanel, STYLESHEET


class ChatScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLESHEET)

    def setUp(self):
        self.panel = ChatPanel([], scroll_to_bottom_on_show=True)
        self.panel.resize(650, 700)
        self.panel.show()
        self.settle()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def settle(self):
        for _ in range(10):
            self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def update(self, messages):
        self.panel.update_messages(messages)
        self.settle()

    def test_new_messages_and_first_progress_scroll_but_progress_updates_do_not(self):
        messages = [{"message": f"Reply {i} " * 60} for i in range(20)]
        self.update(messages)
        scrollbar = self.panel.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 2)
        messages.append({"message": "A new reply"})
        self.update(messages)
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        scrollbar.setValue(scrollbar.maximum() // 2)
        jobs = [{"id": 1, "title": "First job"}]
        self.panel.update_processing_jobs(jobs, {1: "Starting"})
        self.settle()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        scrollbar.setValue(scrollbar.maximum() // 2)
        old_value = scrollbar.value()
        self.panel.update_processing_jobs(jobs, {1: "Working " * 200})
        self.settle()
        self.assertEqual(scrollbar.value(), old_value)
        self.panel.update_processing_jobs(jobs + [{"id": 2, "title": "Second job"}], {})
        self.settle()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_busy_composer_allows_drafts_but_blocks_submission(self):
        composer = self.panel.composer
        submitted = []
        stopped = []
        composer.message_submitted.connect(submitted.append)
        composer.stop_requested.connect(lambda: stopped.append(True))
        for state in ("thinking", "executing", "stopping"):
            composer.set_task_state(state)
            composer.message.clear()
            self.assertTrue(composer.message.isEnabled())
            QTest.keyClicks(composer.message, "Next message")
            QTest.keyClick(composer.message, Qt.Key_Return, Qt.ShiftModifier)
            self.assertEqual(composer.message.toPlainText(), "Next message")
            self.assertEqual(submitted, [])
            composer.send.click()
            self.assertEqual(submitted, [])
            self.assertEqual(composer.message.toPlainText(), "Next message")
        self.assertEqual(len(stopped), 2)
        composer.set_task_state("idle")
        QTest.keyClick(composer.message, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual(submitted, ["Next message"])
        self.assertEqual(composer.message.toPlainText(), "")

    def test_empty_and_cleared_chat_have_no_scroll_range(self):
        scrollbar = self.panel.chat_scroll.verticalScrollBar()
        for messages in ([], [{"message": "   "}], [{"message": "Long reply " * 400}], [], []):
            self.update(messages)
            if messages and messages[0]["message"].strip():
                self.assertGreater(scrollbar.maximum(), 0)
            else:
                self.assertEqual(scrollbar.maximum(), 0)
                self.assertEqual(scrollbar.value(), 0)
        for height in (1000, 400, 700):
            self.panel.resize(650, height)
            self.settle()
            self.assertEqual(scrollbar.maximum(), 0)

    def test_formatted_replies_do_not_reserve_invisible_scroll_space(self):
        # Headings, lists and paragraphs produce different rich-text size hints
        # at the bubble's minimum and maximum widths.
        text = "## Result\n\n" + ("- **Updated section:** extended experience and additional details.\n" * 20)
        messages = [{"message": text, "type": "out"} for _ in range(5)]
        self.update(messages)
        scrollbar = self.panel.chat_scroll.verticalScrollBar()
        for width in (650, 450, 900):
            self.panel.resize(width, 700)
            self.settle()
            self.panel.scroll_to_bottom()
            last = self.panel.message_rows[-1][1]
            bottom = last.geometry().bottom() + 1
            self.assertLessEqual(self.panel.chat_scroll.widget().height() - bottom, self.panel.composer_wrap.height() + 16)
            self.assertLessEqual(bottom - scrollbar.maximum(), self.panel.chat_scroll.viewport().height())
            self.assertGreater(bottom - scrollbar.maximum(), 0)

    def test_updates_end_at_last_bubble_and_preserve_reading_position(self):
        messages = [{"message": f"Reply {i} " * 60} for i in range(20)]
        self.update(messages)
        scrollbar = self.panel.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 2)
        row = next(row for _, row in self.panel.message_rows
                   if row.y() + row.height() > scrollbar.value())
        offset = row.y() - scrollbar.value()
        self.panel.composer.message.setPlainText("Unsent draft")
        messages.insert(0, {"message": "Older history " * 100})
        self.update(messages)
        self.assertEqual(row.y() - scrollbar.value(), offset)
        self.assertEqual(self.panel.composer.message.toPlainText(), "Unsent draft")
        self.panel.scroll_to_bottom()
        for i in range(3):
            messages.append({"message": f"New reply {i}"})
            self.update(messages)
            self.assertEqual(scrollbar.value(), scrollbar.maximum())
            last = self.panel.message_rows[-1][1]
            trailing_space = self.panel.chat_scroll.widget().height() - last.geometry().bottom() - 1
            self.assertLessEqual(trailing_space, self.panel.composer_wrap.height() + 16)
        self.assertLess(self.panel.composer_wrap.y(), self.panel.chat_scroll.geometry().bottom())
        self.panel.scroll_to_bottom()
        self.assertLess(self.panel.message_rows[-1][1].geometry().bottom() - scrollbar.value(), self.panel.composer_wrap.y())


if __name__ == "__main__":
    unittest.main()
