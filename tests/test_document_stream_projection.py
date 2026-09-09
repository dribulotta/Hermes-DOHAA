import copy
import tempfile
import unittest
from pathlib import Path

from tools.document_stream import DocumentEvent as E, StreamStore
from tools.document_stream_request import make_request, canonical
from tools.document_stream_projection import current_document_view, verify_current_view
from tools.document_stream_scoring import ReferenceFact, score_report


class CurrentDocumentViewTests(unittest.TestCase):
    def request(self, events=(), tick=5):
        return make_request({'run_id': 'projection-test', 'at_tick': tick,
                             'events': [e.__dict__ for e in events]},
                            {'task_id': 'projection', 'instruction': 'Report the current answer from authority.',
                             'fact_keys': ['answer']})

    def test_late_revision_does_not_replace_latest(self):
        req = self.request([E('a', 'authority', 2, 1, 'new'), E('b', 'authority', 1, 2, 'old')])
        view, receipt = current_document_view(req)
        self.assertEqual([e['text'] for e in view['observation']['events']], ['new'])
        self.assertEqual(receipt['removed_events']['superseded'], 1)

    def test_expiry_boundary_does_not_revive_older_revision(self):
        req = self.request([E('a', 'authority', 1, 1, 'old'), E('b', 'authority', 2, 2, 'new', expires_at=5)])
        view, receipt = current_document_view(req)
        self.assertEqual(view['observation']['events'], [])
        self.assertEqual(receipt['removed_events']['expired'], 1)

    def test_retraction_does_not_revive_late_old_revision(self):
        req = self.request([E('a', 'authority', 2, 1, '', retracted=True), E('b', 'authority', 1, 2, 'old')])
        view, receipt = current_document_view(req)
        self.assertEqual(view['observation']['events'], [])
        self.assertEqual(receipt['removed_events']['retracted'], 1)

    def test_keeps_all_active_sources_in_delivery_order(self):
        req = self.request([E('a', 'unrelated', 1, 1, 'irrelevant'), E('b', 'authority', 1, 2, 'answer')])
        view, receipt = current_document_view(req)
        self.assertEqual(view, req)
        self.assertEqual(receipt['bytes_saved'], 0)

    def test_repeated_revision_keeps_first_delivery(self):
        req = self.request([E('a', 'authority', 1, 1, 'same'), E('b', 'authority', 1, 2, 'same')])
        view, receipt = current_document_view(req)
        self.assertEqual(view['observation']['events'][0]['event_id'], 'a')
        self.assertEqual(receipt['removed_events']['duplicate_latest'], 1)

    def test_conflicting_revision_is_rejected_before_filtering(self):
        req = self.request([E('a', 'authority', 1, 1, 'one')])
        req['observation']['events'].append(E('b', 'authority', 1, 2, 'two').__dict__)
        with self.assertRaises(ValueError):
            current_document_view(req)

    def test_future_revision_is_rejected_before_filtering(self):
        req = self.request()
        req['observation']['events'].append(E('a', 'authority', 1, 6, 'future').__dict__)
        with self.assertRaises(ValueError):
            current_document_view(req)

    def test_empty_snapshot_is_valid(self):
        req = self.request()
        view, receipt = current_document_view(req)
        self.assertEqual(view, req)
        self.assertTrue(verify_current_view(req, view, receipt))

    def test_bytes_measure_serialized_utf8_without_claiming_tokens(self):
        req = self.request([E('a', 'authority', 1, 1, 'versión antigua'), E('b', 'authority', 2, 2, 'vigente')])
        view, receipt = current_document_view(req)
        self.assertEqual(receipt['input_bytes'], len(canonical(req)))
        self.assertEqual(receipt['output_bytes'], len(canonical(view)))
        self.assertEqual(receipt['bytes_saved'], len(canonical(req)) - len(canonical(view)))
        self.assertGreater(receipt['bytes_saved'], 0)

    def test_preserves_task_and_does_not_alias_or_mutate(self):
        req = self.request([E('a', 'authority', 1, 1, 'content')])
        before = copy.deepcopy(req)
        view, _ = current_document_view(req)
        self.assertEqual(req, before)
        self.assertEqual(view['task'], before['task'])
        view['task']['fact_keys'].append('injected')
        view['observation']['events'][0]['text'] = 'changed'
        self.assertEqual(req, before)

    def test_deterministic_and_receipt_requires_exact_original(self):
        req = self.request([E('a', 'authority', 1, 1, 'first'), E('b', 'authority', 2, 2, 'last')])
        view, receipt = current_document_view(req)
        self.assertEqual((view, receipt), current_document_view(req))
        changed = copy.deepcopy(req)
        changed['observation']['events'][0]['text'] = 'different omitted content'
        self.assertFalse(verify_current_view(changed, view, receipt))
        self.assertTrue(verify_current_view(req, view, receipt))

    def test_tampered_output_receipt_and_boolean_counter_rejected(self):
        req = self.request([E('a', 'authority', 1, 1, 'content')])
        view, receipt = current_document_view(req)
        altered = copy.deepcopy(view)
        altered['task']['instruction'] = 'Different task'
        self.assertFalse(verify_current_view(req, altered, receipt))
        altered_receipt = dict(receipt, output_events=True)
        self.assertFalse(verify_current_view(req, view, altered_receipt))
        altered_receipt = dict(receipt, unrecognized='field')
        self.assertFalse(verify_current_view(req, view, altered_receipt))

    def test_active_sources_and_scoring_preserved(self):
        req = self.request([E('a', 'authority', 1, 1, 'old'), E('b', 'authority', 2, 2, 'current'),
                            E('c', 'withdrawn', 1, 3, '', retracted=True), E('d', 'expired', 1, 4, 'gone', expires_at=5)])
        view, _ = current_document_view(req)
        active = []
        with tempfile.TemporaryDirectory() as directory:
            for i, item in enumerate((req, view)):
                with StreamStore(Path(directory)/f'{i}.sqlite3', run_id='projection-test') as world:
                    world.append(E(**e) for e in item['observation']['events'])
                    active.append(world.active_sources(5))
        self.assertEqual(active[0], active[1])
        reference = [ReferenceFact('answer', 'current', ((('authority', 2),),))]
        for value, revision in [('current', 2), ('wrong', 2), ('current', 1)]:
            report = {'facts': [{'key': 'answer', 'value': value,
                                'evidence': [{'source_id': 'authority', 'revision': revision}]}]}
            self.assertEqual(score_report(report, reference, active[0]), score_report(report, reference, active[1]))


if __name__ == '__main__':
    unittest.main()
