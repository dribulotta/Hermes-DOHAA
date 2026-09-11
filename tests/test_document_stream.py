"""Public development cases; these are not an evaluation holdout."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.document_stream import DocumentEvent, StreamStore


def event(event_id="e1", revision=1, arrived_at=1, **changes):
    fields = dict(event_id=event_id, source_id="notice", revision=revision,
                  arrived_at=arrived_at, text="Delivery is on Tuesday.")
    fields.update(changes)
    return DocumentEvent(**fields)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "stream.sqlite3"
        self.store = StreamStore(self.path, run_id="development-1")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_reopen_retains_same_observation(self):
        self.store.append([event()])
        before = self.store.observation(1)
        self.store.close()
        self.store = StreamStore(self.path, run_id="development-1")
        self.assertEqual(self.store.observation(1), before)

    def test_reopening_other_run_rejected(self):
        with self.assertRaises(ValueError):
            StreamStore(self.path, run_id="development-2")
        self.assertEqual(self.store.append([event()]), 1)

    def test_exact_redelivery_is_idempotent(self):
        self.assertEqual(self.store.append([event(), event()]), 1)
        self.assertEqual(self.store.append([event()]), 0)
        self.assertEqual(len(self.store.observation(1)["events"]), 1)

    def test_conflicting_redelivery_rolls_back_entire_batch(self):
        self.store.append([event()])
        with self.assertRaises(ValueError):
            self.store.append([event("e2", 2, 2), event(text="Changed bytes.")])
        self.assertEqual(len(self.store.observation(5)["events"]), 1)

    def test_same_revision_with_new_envelope_preserves_delivery(self):
        self.store.append([event(), event("e2", 1, 2)])
        self.assertEqual(len(self.store.observation(2)["events"]), 2)
        self.assertEqual(len(self.store.active_sources(2)), 1)

    def test_conflicting_source_revision_rejected(self):
        self.store.append([event()])
        with self.assertRaises(ValueError):
            self.store.append([event("e2", 1, 2, text="Contradictory same revision.")])

    def test_late_old_revision_does_not_replace_new(self):
        self.store.append([event("e2", 2, 1, text="Delivery is on Friday."),
                           event("e1", 1, 2)])
        self.assertEqual(self.store.active_sources(2)["notice"].revision, 2)

    def test_future_delivery_hidden_at_earlier_checkpoint(self):
        self.store.append([event(), event("e2", 2, 5)])
        self.assertEqual(len(self.store.observation(1)["events"]), 1)
        self.assertEqual(self.store.active_sources(1)["notice"].revision, 1)
        self.assertEqual(self.store.observation(0)["events"], [])

    def test_expiry_boundary_does_not_resurrect_old_revision(self):
        self.store.append([event(), event("e2", 2, 2, expires_at=4)])
        self.assertEqual(self.store.active_sources(3)["notice"].revision, 2)
        self.assertEqual(self.store.active_sources(4), {})
        self.assertEqual(self.store.active_sources(100), {})

    def test_already_expired_document_is_not_active(self):
        self.store.append([event(arrived_at=5, expires_at=3)])
        self.assertEqual(self.store.active_sources(5), {})

    def test_retraction_and_later_replacement(self):
        self.store.append([event(), event("e2", 2, 2, text="", retracted=True),
                           event("e3", 3, 4, text="Delivery is cancelled.")])
        self.assertEqual(self.store.active_sources(3), {})
        self.assertEqual(self.store.active_sources(4)["notice"].revision, 3)

    def test_new_delivery_cannot_rewrite_past_arrival(self):
        self.store.append([event(arrived_at=3)])
        with self.assertRaises(ValueError):
            self.store.append([event("e2", 2, 2)])

    def test_failed_input_iteration_rolls_back(self):
        def interrupted():
            yield event()
            raise RuntimeError("synthetic interruption")
        with self.assertRaises(RuntimeError):
            self.store.append(interrupted())
        self.assertEqual(self.store.observation(1)["events"], [])

    def test_event_types_and_bounds(self):
        for changes in ({"revision": True}, {"arrived_at": -1}, {"expires_at": False},
                        {"event_id": "../path"}, {"text": ""},
                        {"text": "x" * 65537}, {"retracted": 1},
                        {"text": "kept", "retracted": True}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                event(**changes)
        with self.assertRaises(ValueError):
            self.store.observation(True)

    def test_oversized_batch_rolls_back(self):
        with self.assertRaises(ValueError):
            self.store.append(event(f"e{i}", i + 1, i) for i in range(257))
        self.assertEqual(self.store.observation(300)["events"], [])

    def test_observation_contains_no_reference_answers(self):
        self.store.append([event()])
        self.assertEqual(set(self.store.observation(1)), {"run_id", "at_tick", "events"})

    def run_crashing_child(self, before_commit):
        self.store.close()
        code = (
            "import os, sys\n"
            "from tools.document_stream import DocumentEvent, StreamStore\n"
            "s=StreamStore(sys.argv[1], run_id='development-1')\n"
            "e=DocumentEvent('e1','notice',1,1,'Delivery is on Tuesday.')\n"
        )
        if before_commit:
            code += "def batch():\n yield e\n os._exit(37)\ns.append(batch())\n"
        else:
            code += "s.append([e])\nos._exit(38)\n"
        result = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                cwd=Path(__file__).resolve().parents[1],
                                env=os.environ.copy(), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 37 if before_commit else 38)
        self.store = StreamStore(self.path, run_id="development-1")

    def test_process_death_before_commit_loses_no_preexisting_record(self):
        self.store.append([event("previous", 1, 0, source_id="other")])
        self.run_crashing_child(before_commit=True)
        self.assertEqual(len(self.store.observation(1)["events"]), 1)
        self.assertEqual(self.store.append([event()]), 1)

    def test_process_death_after_commit_allows_safe_redelivery(self):
        self.run_crashing_child(before_commit=False)
        self.assertEqual(self.store.append([event()]), 0)
        self.assertEqual(len(self.store.observation(1)["events"]), 1)


if __name__ == "__main__":
    unittest.main()
