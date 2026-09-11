"""Known synthetic counterexamples for retrieval; never a native quality study."""
import json

from tools.document_stream import DocumentEvent as E
from tools.document_stream_request import canonical, make_request
from tools.query_memory import STRATEGIES, select_memory


def controls():
    common = dict(task_id='memory-demo', instruction='Report current alpha and beta from their supporting documents.',
                  fact_keys=['alpha', 'beta'])
    examples = [
        ('combined_support', [E('e1', 'combined', 1, 1, 'alpha=11 beta=22'),
                              E('e2', 'alpha-only', 1, 2, 'alpha=11 '+('details '*18)),
                              E('e3', 'beta-only', 1, 3, 'beta=22 '+('details '*18))],
         {'alpha': [{'combined'}, {'alpha-only'}], 'beta': [{'combined'}, {'beta-only'}]},
         {'coverage_per_byte': 2, 'per_key_recency': 1}),
        ('keyword_bait', [E('e1', 'bait', 1, 1, 'alpha beta'),
                          E('e2', 'authority', 1, 2, 'alpha=11 beta=22 '+('context '*25))],
         {'alpha': [{'authority'}], 'beta': [{'authority'}]},
         {'coverage_per_byte': 0, 'per_key_recency': 2}),
    ]
    result = []
    for name, events, references, expected in examples:
        request = make_request(dict(run_id='memory-demo', at_tick=8, events=[e.__dict__ for e in events]), common)
        singleton = make_request(dict(request['observation'], events=[events[1].__dict__]), common)
        budget = len(canonical(singleton))
        rows = []
        for strategy in STRATEGIES:
            # Only this input and the shared cap reach the selector. Reference
            # groups below are evaluator-side development expectations.
            view, receipt = select_memory(request, strategy=strategy, max_bytes=budget)
            selected = {event['source_id'] for event in view['observation']['events']}
            retained = sum(any(group <= selected for group in groups) for groups in references.values())
            if retained != expected[strategy]:
                raise RuntimeError('development control changed')
            rows.append(dict(strategy=strategy, supported_keys_retained=retained,
                             lexical_keys=len(receipt['lexically_covered_keys']),
                             output_bytes=receipt['output_bytes'], budget_bytes=budget))
        result.append(dict(control=name, expected_keys=2, rows=rows))
    return dict(status='controls_passed', kind='synthetic_instrument_validation',
                native_requests=0, architectural_superiority=False, controls=result)


if __name__ == '__main__':
    print(json.dumps(controls(), sort_keys=True))
