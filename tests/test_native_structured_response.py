import copy
import json
import unittest
from unittest.mock import patch

from hermes_dohaa.learning import native_prompt as n, shadow
from hermes_dohaa.learning.native_response_format import (
    DOCUMENT_RESPONSE_CONTRACT, document_response_format, native_request_overrides,
)
from test_native_shadow_adapter import policy, request, wire, encode, digest


class StructuredResponseTests(unittest.TestCase):
    def structured_policy(self):
        value=policy()
        value.update(schema_version='hermes-native-shadow-policy/1.1',
                     response_contract=DOCUMENT_RESPONSE_CONTRACT)
        return value

    def test_legacy_policy_preserves_unstructured_request(self):
        value=policy()
        n.validate_native_policy(encode(value),digest(encode(value)))
        self.assertNotIn('response_format',native_request_overrides(value))
        self.assertEqual(n.validate_wire_request(encode(wire()),request(),value),wire())

    def test_explicit_new_policy_admitted(self):
        value=self.structured_policy()
        self.assertEqual(n.validate_native_policy(encode(value),digest(encode(value))),value)

    def test_missing_unknown_or_downgraded_contract_rejected(self):
        variants=[]
        value=self.structured_policy();value.pop('response_contract');variants.append(value)
        for contract in ('unreviewed',None,{},True):
            value=self.structured_policy();value['response_contract']=contract;variants.append(value)
        value=self.structured_policy();value['schema_version']='hermes-native-shadow-policy/1.0';variants.append(value)
        value=self.structured_policy();value['schema_version']='hermes-native-shadow-policy/9';variants.append(value)
        value=self.structured_policy();value['response_format']={'oracle':'answer'};variants.append(value)
        for value in variants:
            with self.subTest(version=value.get('schema_version')), self.assertRaises((n.NativePromptError,shadow.ShadowError)):
                n.validate_native_policy(encode(value),digest(encode(value)))

    def test_exact_declared_schema_required_on_wire(self):
        value=self.structured_policy()
        raw=wire(p=value);raw['response_format']=document_response_format()
        self.assertEqual(n.validate_wire_request(encode(raw),request(),value),raw)
        for altered in (wire(p=value),dict(raw,response_format={'type':'json_object'}),
                        dict(raw,response_format=None)):
            with self.assertRaises(n.NativePromptError):
                n.validate_wire_request(encode(altered),request(),value)

    def test_legacy_cannot_sneak_in_a_format(self):
        raw=wire();raw['response_format']=document_response_format()
        with self.assertRaises(n.NativePromptError):
            n.validate_wire_request(encode(raw),request(),policy())

    def test_modified_schema_and_boolean_substitution_rejected(self):
        value=self.structured_policy()
        raw=wire(p=value);raw['response_format']=document_response_format()
        variants=[]
        changed=copy.deepcopy(raw);changed['response_format']['json_schema']['strict']='true';variants.append(changed)
        changed=copy.deepcopy(raw);changed['response_format']['json_schema']['strict']=1;variants.append(changed)
        changed=copy.deepcopy(raw);changed['response_format']['json_schema']['schema']['additionalProperties']=True;variants.append(changed)
        changed=copy.deepcopy(raw);changed['response_format']['json_schema']['schema']['properties']['result']['properties']['facts']['items']['properties']['value']={'const':'private answer'};variants.append(changed)
        for changed in variants:
            with self.assertRaises(n.NativePromptError):
                n.validate_wire_request(encode(changed),request(),value)

    def test_overrides_include_format_without_changing_sampling(self):
        value=self.structured_policy()
        overrides=native_request_overrides(value)
        self.assertEqual(overrides,{**{k:value[k] for k in ('seed','temperature','top_p')},
                                    'response_format':document_response_format()})

    def test_returned_schema_mutation_does_not_change_next_request(self):
        first=native_request_overrides(self.structured_policy())
        first['response_format']['json_schema']['schema']['properties'].clear()
        self.assertEqual(native_request_overrides(self.structured_policy())['response_format'],document_response_format())

    def test_schema_source_is_bound_to_bridge_identity(self):
        from pathlib import Path
        real_read=Path.read_bytes
        def modified(path):
            data=real_read(path)
            return data+b'\n# changed response contract\n' if path.name=='native_response_format.py' else data
        before=n.native_bridge_sha256()
        with patch.object(Path,'read_bytes',modified):
            self.assertNotEqual(n.native_bridge_sha256(),before)

    def test_fixed_schema_has_no_external_refs_or_answer_constants(self):
        pending=[document_response_format()]
        while pending:
            item=pending.pop()
            if isinstance(item,dict):
                self.assertFalse({'$ref','$dynamicRef','enum','const','default'} & set(item))
                pending.extend(item.values())
            elif isinstance(item,list):pending.extend(item)


if __name__=='__main__':
    unittest.main()
