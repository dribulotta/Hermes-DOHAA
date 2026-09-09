import copy
from types import SimpleNamespace
import unittest
from hermes_dohaa.learning import native_prompt as n, native_worker as w, shadow
from hermes_dohaa.learning.native_response_format import response_format_for_policy,document_response_format
from test_native_shadow_adapter import policy,encode,digest,request,wire


def explicit_policy():
    return dict(policy(),schema_version='hermes-native-shadow-policy/1.2',
                response_contract='document-stream-proposal/1.0',context_length=8192)


def agent(p):
    return SimpleNamespace(tools=[],valid_tool_names=set(),enabled_toolsets=[],max_iterations=1,
        _tool_use_enforcement=False,_memory_manager=None,_memory_store=None,_memory_enabled=False,
        _user_profile_enabled=False,skip_background_review=True,model=p['model'],provider='lmstudio',
        _config_context_length=8192,context_compressor=SimpleNamespace(context_length=8192))


class NativeLMStudioContextTests(unittest.TestCase):
    def test_new_policy_binds_context_and_fixed_response_contract(self):
        p=explicit_policy()
        self.assertEqual(n.validate_native_policy(encode(p),digest(encode(p))),p)
        self.assertEqual(response_format_for_policy(p),document_response_format())

    def test_invalid_or_missing_context_rejected(self):
        for value in (None,False,0,-1,'8192',512,262145):
            p=explicit_policy()
            if value is None:p.pop('context_length')
            else:p['context_length']=value
            with self.subTest(value=value),self.assertRaises((n.NativePromptError,shadow.ShadowError)):
                n.validate_native_policy(encode(p),digest(encode(p)))

    def test_legacy_policies_do_not_silently_accept_context(self):
        for version in ('1.0','1.1'):
            p=policy();p['schema_version']='hermes-native-shadow-policy/'+version
            if version=='1.1':p['response_contract']='document-stream-proposal/1.0'
            self.assertEqual(w.native_model_configuration(p)['provider'],'custom')
            self.assertNotIn('context_length',w.native_model_configuration(p))
            p['context_length']=8192
            with self.assertRaises((n.NativePromptError,shadow.ShadowError)):
                n.validate_native_policy(encode(p),digest(encode(p)))

    def test_native_config_selects_supported_explicit_lmstudio_context(self):
        p=explicit_policy();config=w.native_model_configuration(p)
        self.assertEqual(config['provider'],'lmstudio')
        self.assertEqual(config['context_length'],p['context_length'])
        self.assertEqual(config['base_url'],p['endpoint'])
        self.assertEqual(config['lmstudio_load_mode'],'jit')
        self.assertEqual(config['api_key'],'credential-supplied-only-in-memory')

    def test_effective_native_provider_and_context_must_match(self):
        p=explicit_policy();a=agent(p)
        self.assertTrue(w.model_profile_checks(a,p))
        for field,value in (('provider','custom'),('_config_context_length',65536),
                            ('_config_context_length',True),('model','other')):
            altered=copy.deepcopy(a);setattr(altered,field,value)
            with self.subTest(field=field):self.assertFalse(w.model_profile_checks(altered,p))
        for value in (65536,True,None):
            altered=copy.deepcopy(a);altered.context_compressor.context_length=value
            self.assertFalse(w.model_profile_checks(altered,p))

    def test_explicit_context_does_not_relax_tool_memory_or_iteration_checks(self):
        p=explicit_policy()
        for field,value in (('tools',['unsafe']),('_memory_enabled',True),('max_iterations',2),
                            ('skip_background_review',False),('_tool_use_enforcement',True)):
            a=agent(p);setattr(a,field,value)
            with self.subTest(field=field):self.assertFalse(w.model_profile_checks(a,p))

    def test_new_policy_keeps_actual_generation_wire_binding(self):
        p=explicit_policy();logical=request();payload=wire(logical,p)
        payload['response_format']=document_response_format()
        n.validate_wire_request(encode(payload),logical,p)
        for field,value in (('max_tokens',256),('model','other'),('response_format',{'type':'json_object'})):
            changed=dict(payload);changed[field]=value
            with self.subTest(field=field),self.assertRaises((n.NativePromptError,shadow.ShadowError)):
                n.validate_wire_request(encode(changed),logical,p)
