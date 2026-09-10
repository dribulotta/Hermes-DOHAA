"""Prospective scope checks: no model traffic and no historical artifacts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import controlled_route_protocol as boundary

# This checkout explicitly exercises the newly reviewed profile. The fixture
# does not compute or substitute the expected source commitment at runtime.
FIXTURE_PIPELINE = 'verified-tool-admission/1.2'
FIXTURE_FILES = boundary.INTEGRATED_AUDITED_FILES
FIXTURE_SOURCE = boundary.BOUNDED_AUDITED_SOURCE_SHA256

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def protocol(outcome='effect_outcome'):
    shared = dict(input_sha256='1'*64, proposal_sha256='2'*64,
                  initial_state_sha256='3'*64, permissions_sha256='4'*64,
                  policy_sha256='5'*64, fault_schedule_sha256='6'*64,
                  max_native_calls=1, max_output_tokens=1024,
                  reasoning_strategy='none')
    return dict(schema_version='hermes-route-attribution-protocol/1.0',
                pipeline=FIXTURE_PIPELINE, outcome=outcome,
                study_kind='fresh_synthetic_conformance',
                pairing='identical_proposal_independent_state',
                arms=[dict(shared, route='dohaa'), dict(shared, route='simple')])


def validate(raw):
    data = encode(raw)
    return boundary.validate_protocol(data, hashlib.sha256(data).hexdigest())


class RouteProtocolTests(unittest.TestCase):
    def test_known_source_supports_only_downstream_conformance(self):
        for outcome in ('proposal_admission', 'effect_outcome', 'recovery_outcome'):
            with self.subTest(outcome=outcome):
                report = validate(protocol(outcome))
                self.assertEqual(report['status'], 'scope_validated')
                self.assertEqual(report['intervention_stage'], 'after_native_terminal')
                self.assertEqual(report['outcome'], outcome)
                self.assertFalse(report['quality_claim_supported'])
                self.assertFalse(report['runtime_attested'])
                self.assertFalse(report['execution_authorized'])
                self.assertEqual(report['native_requests'], 0)
                self.assertEqual(report['independent_llm_observations'], 0)

    def test_raw_answer_quality_is_not_changed_by_downstream_route(self):
        with self.assertRaisesRegex(boundary.ProtocolError, 'outcome_precedes_intervention'):
            validate(protocol('native_answer_quality'))

    def test_declared_upstream_intervention_cannot_override_audited_pipeline(self):
        raw = protocol(); raw['intervention_stage'] = 'before_native_generation'
        with self.assertRaisesRegex(boundary.ProtocolError, 'protocol_fields'):
            validate(raw)

    def test_unknown_pipeline_or_outcome_is_not_inferred(self):
        for field, value in (('pipeline', 'future-memory/1.0'), ('outcome', 'general_intelligence')):
            raw = protocol(); raw[field] = value
            with self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_observational_live_and_historical_studies_are_outside_this_increment(self):
        for kind in ('native_quality_cohort', 'historical_replay', 'observational'):
            raw = protocol(); raw['study_kind'] = kind
            with self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_equal_digest_labels_do_not_authorize_reusing_state_or_unpaired_calls(self):
        for pairing in ('same_mutable_store', 'independent_native_calls', 'historical_terminals'):
            raw = protocol(); raw['pairing'] = pairing
            with self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_shared_information_resources_permissions_and_faults_must_match(self):
        for key in protocol()['arms'][0]:
            if key == 'route': continue
            raw = protocol()
            old = raw['arms'][1][key]
            raw['arms'][1][key] = ('a'*64 if key.endswith('_sha256') else
                                  'medium_default_on' if key == 'reasoning_strategy' else old+1)
            with self.subTest(key=key), self.assertRaisesRegex(boundary.ProtocolError, 'unequal_arm_conditions'):
                validate(raw)

    def test_routes_are_exactly_one_dohaa_and_one_simple_in_either_order(self):
        raw = protocol(); raw['arms'].reverse()
        self.assertEqual(validate(raw)['status'], 'scope_validated')
        for arms in (raw['arms'][:1], [raw['arms'][0]]*2, raw['arms']*2):
            changed = dict(raw, arms=arms)
            with self.assertRaises(boundary.ProtocolError): validate(changed)

    def test_only_one_native_proposal_and_bounded_tokens_are_supported(self):
        for key, value in (('max_native_calls', 2), ('max_native_calls', True),
                           ('max_output_tokens', True), ('max_output_tokens', 0),
                           ('max_output_tokens', 65537)):
            raw = protocol()
            for arm in raw['arms']: arm[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_generic_medium_is_not_an_attested_binary_mode(self):
        raw = protocol()
        for arm in raw['arms']: arm['reasoning_strategy'] = 'medium'
        with self.assertRaises(boundary.ProtocolError): validate(raw)
        for arm in raw['arms']: arm['reasoning_strategy'] = 'medium_default_on'
        result = validate(raw)
        self.assertTrue(result['on_depends_on_declared_default'])
        self.assertFalse(result['effective_mode_attested'])

    def test_unknown_fields_and_malformed_commitments_are_rejected(self):
        for value in ('x'*64, '0'*63, None, True):
            raw = protocol(); raw['arms'][0]['proposal_sha256'] = value
            with self.assertRaises(boundary.ProtocolError): validate(raw)
        raw = protocol(); raw['arms'][0]['source_sha256'] = 'a'*64
        with self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_input_bytes_are_bound_before_decoding(self):
        with self.assertRaisesRegex(boundary.ProtocolError, 'protocol_digest'):
            boundary.validate_protocol(encode(protocol()), '0'*64)

    def test_ambiguous_nonfinite_or_oversized_json_fails_closed(self):
        for data in (b'{"outcome":"effect_outcome","outcome":"native_answer_quality"}',
                     b'{"arms":NaN}', b'['*2000, b' '*65537):
            with self.assertRaises(boundary.ProtocolError):
                boundary.validate_protocol(data, hashlib.sha256(data).hexdigest())

    def test_caller_cannot_supply_a_replacement_source_root_or_audit(self):
        for key in ('source_root', 'source_sha256', 'audit'):
            raw = protocol(); raw[key] = 'trusted-by-author'
            with self.assertRaises(boundary.ProtocolError): validate(raw)

    def test_changed_source_cannot_keep_the_old_causal_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in FIXTURE_FILES:
                target = root/relative; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((boundary._ROOT/relative).read_bytes())
            target = root/'tools/controlled_tool_routes.py'
            target.write_bytes(target.read_bytes().replace(b'max_attempts=1', b'max_attempts=2'))
            with patch.object(boundary, '_ROOT', root):
                with self.assertRaisesRegex(boundary.ProtocolError, 'audited_source_changed'):
                    validate(protocol())

    def test_missing_source_fails_without_a_default_audit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(boundary, '_ROOT', Path(directory)):
            with self.assertRaisesRegex(boundary.ProtocolError, 'audited_source_unavailable'):
                validate(protocol())

    def test_validated_report_is_bound_to_original_protocol_and_audited_source(self):
        raw = protocol(); result = validate(raw)
        self.assertEqual(result['protocol_sha256'], hashlib.sha256(encode(raw)).hexdigest())
        self.assertEqual(result['source_sha256'], FIXTURE_SOURCE)
        raw['arms'][0]['route'] = 'other'
        self.assertEqual(result['routes'], ['dohaa', 'simple'])

    def test_each_explicit_profile_binds_its_own_file_list_and_commitment(self):
        # Mocked source measurements exercise dispatch, not source attestation.
        profiles = [('verified-tool-admission/1.0', boundary.AUDITED_FILES, boundary.AUDITED_SOURCE_SHA256),
                    ('verified-tool-admission/1.1', boundary.INTEGRATED_AUDITED_FILES, boundary.INTEGRATED_AUDITED_SOURCE_SHA256),
                    ('verified-tool-admission/1.2', boundary.INTEGRATED_AUDITED_FILES, boundary.BOUNDED_AUDITED_SOURCE_SHA256)]
        for label, files, digest in profiles:
            raw = protocol(); raw['pipeline'] = label
            with patch.object(boundary, '_source_fingerprint', return_value=digest) as fingerprint:
                result = validate(raw)
                fingerprint.assert_called_once_with(files)
                self.assertEqual(result['pipeline'], label)
                self.assertEqual(result['source_sha256'], digest)
            for other in (value for _, _, value in profiles if value != digest):
                with patch.object(boundary, '_source_fingerprint', return_value=other):
                    with self.assertRaisesRegex(boundary.ProtocolError, 'audited_source_changed'):
                        validate(raw)

    def test_actual_checkout_does_not_implicitly_select_the_other_profile(self):
        for label in ('verified-tool-admission/1.0', 'verified-tool-admission/1.1'):
            raw = protocol(); raw['pipeline'] = label
            with self.subTest(label=label), self.assertRaisesRegex(
                    boundary.ProtocolError, 'audited_source_(changed|unavailable)'):
                validate(raw)

    def test_integrated_profile_measures_and_requires_new_evidence_dependency(self):
        dependency = 'src/hermes_dohaa/assurance/evidence_policy.py'
        self.assertNotIn(dependency, boundary.AUDITED_FILES)
        self.assertIn(dependency, boundary.INTEGRATED_AUDITED_FILES)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in boundary.INTEGRATED_AUDITED_FILES:
                target = root/relative; target.parent.mkdir(parents=True, exist_ok=True)
                original = boundary._ROOT/relative
                target.write_bytes(original.read_bytes() if original.exists() else b'synthetic dependency')
            with patch.object(boundary, '_ROOT', root):
                before = boundary._source_fingerprint(boundary.INTEGRATED_AUDITED_FILES)
                target = root/dependency
                target.write_bytes(target.read_bytes()+b'\n# changed dependency\n')
                self.assertNotEqual(boundary._source_fingerprint(boundary.INTEGRATED_AUDITED_FILES), before)
                raw = protocol()
                with self.assertRaisesRegex(boundary.ProtocolError, 'audited_source_changed'):
                    validate(raw)
                target.unlink()
                with self.assertRaisesRegex(boundary.ProtocolError, 'audited_source_unavailable'):
                    validate(raw)

    def test_untyped_or_unreviewed_profile_has_no_dynamic_fallback(self):
        for label in (None, [], {}, True, 'verified-tool-admission/1.3'):
            raw = protocol(); raw['pipeline'] = label
            with self.assertRaisesRegex(boundary.ProtocolError, 'unsupported_pipeline'):
                validate(raw)
