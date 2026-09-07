"""One unprivileged native-Hermes generation. Input is private JSON over stdin."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import resource
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import shadow
from .native_prompt import (MAX_WIRE, NativePromptError, native_bridge_sha256, parse_wire_response,
                            prompt_frame, validate_native_policy, validate_request, validate_wire_request)


class BudgetStop(RuntimeError):
    pass


def profile_checks(agent):
    missing = object()
    checks = {
        'tools': getattr(agent, 'tools', missing) == [],
        'tool_names': getattr(agent, 'valid_tool_names', missing) in (set(), frozenset(), []),
        'toolsets': getattr(agent, 'enabled_toolsets', missing) == [],
        'iterations': type(getattr(agent, 'max_iterations', None)) is int and agent.max_iterations == 1,
        'tool_enforcement': getattr(agent, '_tool_use_enforcement', missing) is False,
        'memory_manager': getattr(agent, '_memory_manager', missing) is None,
        'memory_store': getattr(agent, '_memory_store', missing) is None,
        'memory': getattr(agent, '_memory_enabled', missing) is False,
        'user_profile': getattr(agent, '_user_profile_enabled', missing) is False,
        'background_review': getattr(agent, 'skip_background_review', missing) is True,
    }
    return all(checks.values())


class Guard:
    def __init__(self, policy, logical, api_key):
        self.policy, self.logical, self.api_key = policy, logical, api_key
        self.posts, self.denied, self.metadata = 0, 0, 0
        self.request_bytes = self.response_bytes = b''
        self.server_finished, self.parsed = False, None

    def install(self):
        import httpx
        endpoint = urlsplit(self.policy['endpoint'])
        original_send = httpx.Client.send
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        owner = self

        def address_allowed(address):
            return (isinstance(address, tuple) and len(address) >= 2
                    and address[0] == endpoint.hostname and address[1] == endpoint.port)

        def connect(sock, address):
            if not address_allowed(address):
                raise NativePromptError('native_shadow.network_destination')
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if not address_allowed(address):
                raise NativePromptError('native_shadow.network_destination')
            return original_connect_ex(sock, address)

        def send(client, request, **kwargs):
            destination = urlsplit(str(request.url))
            if (destination.scheme, destination.netloc) != (endpoint.scheme, endpoint.netloc) or destination.query or destination.fragment:
                raise NativePromptError('native_shadow.network_destination')
            generation = request.method == 'POST' and destination.path == '/v1/chat/completions'
            if not generation:
                allowed = request.method == 'GET' and destination.path in {'/v1/models', '/api/v1/models'}
                if request.method == 'POST' and destination.path == '/api/show':
                    value = shadow._json(request.content, shadow._hash(request.content))
                    allowed = value == {'name': owner.policy['model']}
                if not allowed or owner.metadata >= 8:
                    raise NativePromptError('native_shadow.unexpected_request')
                owner.metadata += 1
            else:
                # Retain an attempted payload for private rejection diagnosis;
                # posts counts only requests actually authorized for dispatch.
                if not owner.posts and len(request.content) <= MAX_WIRE:
                    owner.request_bytes = request.content
                validate_wire_request(request.content, owner.logical, owner.policy)
                if request.headers.get('authorization') != 'Bearer ' + owner.api_key:
                    raise NativePromptError('native_shadow.credential_binding')
                if owner.posts:
                    owner.denied += 1
                    raise BudgetStop()
                owner.posts += 1
                owner.request_bytes = request.content
            timeout = owner.policy['request_timeout_seconds']
            request.extensions['timeout'] = {name: timeout for name in ('connect', 'read', 'write', 'pool')}
            kwargs['stream'] = True
            kwargs['follow_redirects'] = False
            response = original_send(client, request, **kwargs)
            parts, size = [], 0
            try:
                for part in response.iter_bytes(chunk_size=65536):
                    size += len(part)
                    if size > MAX_WIRE:
                        raise NativePromptError('native_shadow.wire_limit')
                    parts.append(part)
            finally:
                response.close()
            data = b''.join(parts)
            if generation:
                owner.response_bytes = data
                if response.status_code != 200:
                    raise NativePromptError('native_shadow.http_status')
                owner.parsed = parse_wire_response(data, owner.policy['model'])
                owner.server_finished = True
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}}
            return httpx.Response(response.status_code, headers=headers, content=data, request=request)

        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
        httpx.Client.send = send

    def deny_summary(self, *args, **kwargs):
        self.denied += 1
        raise BudgetStop()


def execute(config):
    policy = validate_native_policy(shadow._canonical(config['policy']), config['runtime_policy_sha256'])
    request = validate_request(shadow._canonical(config['request']), config['collection_policy_sha256'])
    if shadow._hash(shadow._canonical(request)) != config['request_sha256']:
        raise NativePromptError('native_shadow.request_invalid')
    if os.geteuid() != policy['worker_uid'] or os.getegid() != policy['worker_gid'] or os.geteuid() == 0:
        raise NativePromptError('native_shadow.worker_identity')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
    profile = Path(config['profile']).resolve()
    if Path.cwd() != profile or os.environ.get('HERMES_HOME') != str(profile):
        raise NativePromptError('native_shadow.worker_profile')
    guard = Guard(policy, request, config['api_key'])
    guard.install()
    import yaml
    from toolsets import TOOLSETS
    configuration = {
        'model': {'default': policy['model'], 'provider': 'custom', 'base_url': policy['endpoint'],
                  'api_key': 'credential-supplied-only-in-memory', 'lmstudio_load_mode': 'jit'},
        'auxiliary': {'title_generation': {'enabled': False}, 'background_review': {'enabled': False}},
        'platform_toolsets': {'api_server': []},
        'agent': {'max_turns': 1, 'tool_use_enforcement': False, 'execution_guidance': False,
                  'run_budget_seconds': policy['request_timeout_seconds'], 'disabled_toolsets': sorted(TOOLSETS)},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'provider': ''},
        'mcp_servers': {},
        'gateway': {'api_server': {'max_concurrent_runs': 1, 'direct_model_requests': True}},
    }
    (profile / 'config.yaml').write_text(yaml.safe_dump(configuration), encoding='utf-8')
    (profile / 'SOUL.md').write_text('Return only the requested final JSON object.\n', encoding='utf-8')
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    agent = None
    native_result = {}
    result = {'schema_version': 'hermes-native-shadow-trace/1.0',
              'runtime_policy_sha256': config['runtime_policy_sha256'], 'bridge_sha256': native_bridge_sha256(),
              'request_sha256': config['request_sha256'], 'uid': os.geteuid(), 'gid': os.getegid(),
              'identity_isolated': os.getresuid() == (policy['worker_uid'],) * 3
                  and os.getresgid() == (policy['worker_gid'],) * 3 and os.getgroups() == [],
              'profile_passed': False, 'agent_class': None}
    try:
        agent = adapter._create_agent(requested_model=policy['model'], requested_provider='custom',
            route={'model': policy['model'], 'provider': 'custom', 'base_url': policy['endpoint'],
                   'api_key': config['api_key']},
            model_options={'reasoning': {'enabled': policy['reasoning_effort'] != 'none',
                                         'effort': policy['reasoning_effort']}},
            ephemeral_system_prompt=prompt_frame(request), session_id=request['request_id'])
        agent.max_tokens = policy['max_tokens']
        agent.skip_background_review = True
        agent.request_overrides = {key: policy[key] for key in ('seed', 'temperature', 'top_p')}
        agent._handle_max_iterations = guard.deny_summary
        result['agent_class'] = type(agent).__name__
        result['profile_passed'] = profile_checks(agent) and agent.model == policy['model']
        if not result['profile_passed']:
            raise NativePromptError('native_shadow.profile_rejected')
        native_result = agent.run_conversation(user_message=request['input'], conversation_history=[])
    except Exception:
        result['native_error'] = True
    finally:
        if adapter._session_db is not None:
            adapter._session_db.close()
    native_completed = (isinstance(native_result, dict) and not native_result.get('failed')
                        and not native_result.get('partial') and native_result.get('completed') is not False
                        and isinstance(native_result.get('final_response'), str))
    visible = native_result.get('final_response', '') if isinstance(native_result, dict) else ''
    matches = guard.parsed is not None and isinstance(visible, str) and visible.strip() == guard.parsed['content'].strip()
    result.update(server_finished=guard.server_finished, actual_requests=guard.posts,
                  denied_continuations=guard.denied, metadata_requests=guard.metadata,
                  native_completed=native_completed, content_matches_wire=matches,
                  wire_request_base64=base64.b64encode(guard.request_bytes).decode('ascii'),
                  wire_response_base64=base64.b64encode(guard.response_bytes).decode('ascii'))
    return result


def main():
    wire_output = sys.stdout
    try:
        data = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise NativePromptError('native_shadow.input_limit')
        config = shadow._json(data, shadow._hash(data))
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(config)
        wire_output.write(shadow._canonical(result).decode('utf-8'))
        wire_output.flush()
        return 0
    except Exception:
        wire_output.write('{"status":"failed","error_code":"native_shadow.worker_failed"}')
        wire_output.flush()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
