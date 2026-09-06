import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.check_hermes_profile import assess_agent, main


def isolated_agent():
    return SimpleNamespace(
        tools=[], valid_tool_names=set(), enabled_toolsets=[], max_iterations=1,
        _tool_use_enforcement=False, _memory_manager=None, _memory_store=None,
        _memory_enabled=False, _user_profile_enabled=False,
    )


class RuntimeProfileTests(unittest.TestCase):
    def test_empty_constructed_agent_passes(self):
        result = assess_agent(isolated_agent())
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(all(result['checks'].values()))

    def test_actual_tools_fail_even_when_configured_toolsets_are_empty(self):
        agent = isolated_agent()
        agent.tools = [{'function': {'name': 'private-tool-name'}}]
        agent.valid_tool_names = {'private-tool-name'}
        result = assess_agent(agent)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['checks']['no_tool_definitions'])
        self.assertFalse(result['checks']['no_callable_tool_names'])
        self.assertNotIn('private-tool-name', json.dumps(result))

    def test_memory_and_enforcement_fail_closed(self):
        for name, value in (
            ('_memory_manager', object()), ('_memory_store', object()),
            ('_memory_enabled', True), ('_user_profile_enabled', True),
            ('_tool_use_enforcement', 'auto'), ('enabled_toolsets', None),
        ):
            with self.subTest(name=name):
                agent = isolated_agent()
                setattr(agent, name, value)
                self.assertEqual(assess_agent(agent)['status'], 'failed')

    def test_missing_runtime_fields_are_not_assumed_safe(self):
        for field in vars(isolated_agent()):
            with self.subTest(field=field):
                agent = isolated_agent()
                delattr(agent, field)
                self.assertEqual(assess_agent(agent)['status'], 'failed')

    def test_iteration_budget_must_be_exactly_one_integer(self):
        for value in (0, -1, 2, True, 1.0, '1', None):
            with self.subTest(value=value):
                agent = isolated_agent()
                agent.max_iterations = value
                self.assertEqual(assess_agent(agent)['status'], 'failed')

    def test_constructor_failures_do_not_disclose_native_diagnostics(self):
        def fail_constructor(*args, **kwargs):
            print('secret-native-diagnostic')
            raise RuntimeError('secret-connection-details')
        modules = {
            'gateway': SimpleNamespace(),
            'gateway.config': SimpleNamespace(PlatformConfig=lambda **kwargs: None),
            'gateway.platforms': SimpleNamespace(),
            'gateway.platforms.api_server': SimpleNamespace(APIServerAdapter=fail_constructor),
        }
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'config.yaml').write_text('{}')
            output = io.StringIO()
            with patch.dict(sys.modules, modules), patch.dict(os.environ), patch('os.chdir'), redirect_stdout(output):
                code = main(['--profile-dir', directory])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertNotIn('secret', output.getvalue())


if __name__ == '__main__':
    unittest.main()
