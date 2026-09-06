#!/usr/bin/env python3
"""Check a constructed Hermes API agent in a disposable test profile.

Use the Python environment containing the pinned Hermes Agent installation.
Construction can create runtime state, so never point this at a live profile.
The checker does not call run_conversation or request a model completion.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any, Sequence


_MISSING = object()


def assess_agent(agent: Any) -> dict[str, Any]:
    """Fail closed on missing fields; expose booleans, never runtime secrets."""
    tools = getattr(agent, 'tools', _MISSING)
    names = getattr(agent, 'valid_tool_names', _MISSING)
    toolsets = getattr(agent, 'enabled_toolsets', _MISSING)
    iterations = getattr(agent, 'max_iterations', _MISSING)
    checks = {
        'no_tool_definitions': isinstance(tools, (list, tuple)) and len(tools) == 0,
        'no_callable_tool_names': isinstance(names, (set, frozenset, list, tuple)) and len(names) == 0,
        'explicit_empty_toolsets': isinstance(toolsets, (list, tuple)) and len(toolsets) == 0,
        'single_iteration': type(iterations) is int and iterations == 1,
        'no_forced_tool_use': getattr(agent, '_tool_use_enforcement', _MISSING) is False,
        'no_memory_manager': getattr(agent, '_memory_manager', _MISSING) is None,
        'no_memory_store': getattr(agent, '_memory_store', _MISSING) is None,
        'memory_disabled': getattr(agent, '_memory_enabled', _MISSING) is False,
        'user_profile_disabled': getattr(agent, '_user_profile_enabled', _MISSING) is False,
    }
    return {
        'schema_version': 'hermes-profile-check/1.0',
        'status': 'passed' if all(checks.values()) else 'failed',
        'scope': 'constructed-api-agent-only',
        'model_completion_requested': False,
        'checks': checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-dir', type=Path, required=True,
                        help='disposable Hermes profile containing config.yaml')
    args = parser.parse_args(argv)
    profile = args.profile_dir.resolve()
    if not profile.is_dir() or not (profile / 'config.yaml').is_file():
        parser.error('a test profile with config.yaml is required')
    os.environ['HERMES_HOME'] = str(profile)
    os.chdir(profile)
    try:
        # Native constructor diagnostics may contain endpoints. Do not export them.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from gateway.config import PlatformConfig
            from gateway.platforms.api_server import APIServerAdapter

            adapter = APIServerAdapter(PlatformConfig(enabled=True))
            agent = adapter._create_agent(
                ephemeral_system_prompt='Return only the requested JSON object.',
                model_options={'reasoning': {'enabled': False, 'effort': 'none'}},
            )
            report = assess_agent(agent)
    except Exception as exc:
        report = {
            'schema_version': 'hermes-profile-check/1.0', 'status': 'failed',
            'scope': 'constructed-api-agent-only', 'model_completion_requested': False,
            'error_code': 'profile.construction_failed', 'error_type': type(exc).__name__,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
