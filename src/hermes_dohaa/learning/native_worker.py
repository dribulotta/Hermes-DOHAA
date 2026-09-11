"""One unprivileged native-Hermes generation. Input is private JSON over stdin."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import shadow
from .native_tool_contract import TOOL_POLICY_VERSIONS
from .native_reasoning import (binary_reasoning, compatibility_reasoning, reasoning_enabled,
                               reasoning_request_evidence, verify_binary_catalog)
from .native_progress import ProgressWriter
from .native_response_format import native_request_overrides
from .native_prompt import (FAILURE_CODES, MAX_WIRE, MAX_RESPONSE_WIRE, MAX_WORKER_TRACE,
                            NativePromptError, native_bridge_sha256, parse_wire_response,
                            prompt_frame, validate_native_policy, validate_request, validate_wire_request)


class BudgetStop(RuntimeError):
    pass


@contextlib.contextmanager
def setup_phase(trace, stage):
    """Finite diagnostic only; never terminal/cleanup authority or exception text."""
    if stage not in ('create_agent', 'configure_agent', 'profile_check'):
        raise ValueError('unknown_setup_stage')
    try:
        yield
    except Exception as exc:
        kind = 'other'
        for exception_type, label in ((PermissionError, 'permission'), (TimeoutError, 'timeout'),
                                      (OSError, 'os'), (ValueError, 'value'), (RuntimeError, 'runtime')):
            if isinstance(exc, exception_type):
                kind = label
                break
        if trace.get('setup_failure') is None:
            trace['setup_failure'] = {'stage': stage, 'exception_kind': kind}
        raise


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


def native_model_configuration(policy):
    model = {'default': policy['model'], 'provider': 'custom', 'base_url': policy['endpoint'],
             'api_key': 'credential-supplied-only-in-memory', 'lmstudio_load_mode': 'jit'}
    if policy['schema_version'] in ('hermes-native-shadow-policy/1.2', *TOOL_POLICY_VERSIONS):
        model.update(provider='lmstudio', context_length=policy['context_length'])
    return model


def model_profile_checks(agent, policy):
    if not profile_checks(agent) or agent.model != policy['model']:
        return False
    if policy['schema_version'] in ('hermes-native-shadow-policy/1.2', *TOOL_POLICY_VERSIONS):
        configured = getattr(agent, '_config_context_length', None)
        effective = getattr(getattr(agent, 'context_compressor', None), 'context_length', None)
        return (getattr(agent, 'provider', None) == 'lmstudio'
                and type(configured) is int and configured == policy['context_length']
                and type(effective) is int and effective == policy['context_length'])
    return True


class Guard:
    def __init__(self, policy, logical, api_key, progress=None):
        self.policy, self.logical, self.api_key = policy, logical, api_key
        self.posts, self.denied, self.metadata = 0, 0, 0
        self.request_bytes = self.response_bytes = b''
        self.server_finished, self.parsed = False, None
        self.response_observed = 0
        self.generation_failure = None
        self.progress = progress
        self.reasoning_catalog_bytes = b''
        self.reasoning_preflight_passed = False
        self.reasoning_preflight_failed = False

    def report_progress(self, stage, http_status=None):
        if self.progress is not None:
            self.progress.emit(stage, generation_requests=self.posts,
                request_bytes=len(self.request_bytes), response_bytes_observed=self.response_observed,
                response_bytes_retained=len(self.response_bytes), http_status=http_status)

    def record_failure(self, exc, stage, status, httpx):
        # Preserve the first dispatched-generation failure. Native retries and
        # summary attempts are denied separately and cannot erase its cause.
        if self.generation_failure is not None:
            return
        code = 'native_shadow.transport_error'
        if isinstance(exc, NativePromptError) and exc.code in FAILURE_CODES:
            code = exc.code
        elif isinstance(exc, httpx.TimeoutException):
            code = 'native_shadow.transport_timeout'
        elif stage == 'parse':
            code = 'native_shadow.response_invalid'
        self.generation_failure = {
            'code': code, 'stage': stage, 'request_bytes': len(self.request_bytes),
            'response_bytes_observed': self.response_observed,
            'response_bytes_retained': len(self.response_bytes),
            'http_status': status if type(status) is int and 100 <= status <= 599 else None}

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
            capability_read = (binary_reasoning(owner.policy) and request.method == 'GET'
                               and destination.path == '/api/v1/models')
            generation = request.method == 'POST' and destination.path == '/v1/chat/completions'
            if not generation:
                allowed = request.method == 'GET' and destination.path in {'/v1/models', '/api/v1/models'}
                if request.method == 'POST' and destination.path == '/api/show':
                    value = shadow._json(request.content, shadow._hash(request.content))
                    allowed = value == {'name': owner.policy['model']}
                if not allowed or owner.metadata >= 8:
                    raise NativePromptError('native_shadow.unexpected_request')
                if capability_read and owner.reasoning_preflight_failed:
                    raise NativePromptError('native_shadow.reasoning_capability_unverified')
                owner.metadata += 1
            else:
                # Retain an attempted payload for private rejection diagnosis;
                # posts counts only requests actually authorized for dispatch.
                if not owner.posts and len(request.content) <= MAX_WIRE:
                    owner.request_bytes = request.content
                validate_wire_request(request.content, owner.logical, owner.policy)
                if binary_reasoning(owner.policy) and (not owner.reasoning_preflight_passed or owner.reasoning_preflight_failed):
                    raise NativePromptError('native_shadow.reasoning_capability_unverified')
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
            response, stage, failed = None, 'send', False
            data = bytearray()
            capture_limit = MAX_RESPONSE_WIRE if generation else MAX_WIRE
            try:
                owner.report_progress('send' if generation else 'metadata_send')
                response = original_send(client, request, **kwargs)
                stage = 'read'
                owner.report_progress('read' if generation else 'metadata_read', response.status_code)
                for part in response.iter_bytes():
                    # Retain decoded chunks as they arrive, including a bounded
                    # prefix of an oversized chunk, even when iteration fails.
                    remaining = capture_limit - len(data)
                    data.extend(part[:remaining])
                    if generation:
                        owner.response_observed = min(2**63-1, owner.response_observed + len(part))
                        owner.response_bytes = bytes(data)
                        owner.report_progress('read', response.status_code)
                    if len(part) > remaining:
                        raise NativePromptError('native_shadow.wire_limit')
                if capability_read:
                    owner.reasoning_catalog_bytes = bytes(data)
                    try:
                        if response.status_code != 200:
                            raise ValueError('capability_http_status')
                        verify_binary_catalog(bytes(data), owner.policy)
                    except (ValueError, shadow.ShadowError) as exc:
                        raise NativePromptError('native_shadow.reasoning_capability_unverified') from exc
                if generation:
                    stage = 'http_status'
                    owner.report_progress(stage, response.status_code)
                    if response.status_code != 200:
                        raise NativePromptError('native_shadow.http_status')
                    stage = 'parse'
                    owner.report_progress(stage, response.status_code)
                    owner.parsed = parse_wire_response(bytes(data), owner.policy['model'])
            except Exception as exc:
                failed = True
                if capability_read:
                    owner.reasoning_preflight_failed = True
                    owner.reasoning_preflight_passed = False
                if generation:
                    owner.record_failure(exc, stage, getattr(response, 'status_code', None), httpx)
                raise
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception as exc:
                        if not failed:
                            if capability_read:
                                owner.reasoning_preflight_failed = True
                                owner.reasoning_preflight_passed = False
                            if generation:
                                owner.record_failure(exc, 'close', response.status_code, httpx)
                            raise
            if capability_read:
                owner.reasoning_preflight_passed = True
            if generation:
                owner.server_finished = True
                owner.report_progress('guard_returned', response.status_code)
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}}
            return httpx.Response(response.status_code, headers=headers, content=bytes(data), request=request)

        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
        httpx.Client.send = send

    def deny_summary(self, *args, **kwargs):
        self.denied += 1
        raise BudgetStop()


def execute(config):
    import resource
    policy = validate_native_policy(shadow._canonical(config['policy']), config['runtime_policy_sha256'])
    request = validate_request(shadow._canonical(config['request']), config['collection_policy_sha256'])
    if shadow._hash(shadow._canonical(request)) != config['request_sha256']:
        raise NativePromptError('native_shadow.request_invalid')
    if os.geteuid() != policy['worker_uid'] or os.getegid() != policy['worker_gid'] or os.geteuid() == 0:
        raise NativePromptError('native_shadow.worker_identity')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WORKER_TRACE, MAX_WORKER_TRACE))
    profile = Path(config['profile']).resolve()
    if Path.cwd() != profile or os.environ.get('HERMES_HOME') != str(profile):
        raise NativePromptError('native_shadow.worker_profile')
    progress = ProgressWriter(config.get('progress_fd'),
        {'request_sha256': config['request_sha256'],
         'runtime_policy_sha256': config['runtime_policy_sha256'],
         'bridge_sha256': policy['bridge_sha256']}, policy['worker_timeout_seconds'] * 1000)
    guard = Guard(policy, request, config['api_key'], progress)
    guard.report_progress('setup')
    guard.install()
    import yaml
    from toolsets import TOOLSETS
    model_configuration = native_model_configuration(policy)
    configuration = {
        'model': model_configuration,
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
        with setup_phase(result, 'create_agent'):
            agent = adapter._create_agent(requested_model=policy['model'], requested_provider=model_configuration['provider'],
                route={'model': policy['model'], 'provider': model_configuration['provider'], 'base_url': policy['endpoint'],
                       'api_key': config['api_key']},
                model_options={'reasoning': {'enabled': reasoning_enabled(policy),
                                             'effort': policy['reasoning_effort']}},
                ephemeral_system_prompt=prompt_frame(request), session_id=request['request_id'])
        with setup_phase(result, 'configure_agent'):
            agent.max_tokens = policy['max_tokens']
            agent.skip_background_review = True
            agent.request_overrides = native_request_overrides(policy)
            agent._handle_max_iterations = guard.deny_summary
            result['agent_class'] = type(agent).__name__
        with setup_phase(result, 'profile_check'):
            result['profile_passed'] = model_profile_checks(agent, policy)
            if not result['profile_passed']:
                raise NativePromptError('native_shadow.profile_rejected')
        guard.report_progress('agent_ready')
        native_result = agent.run_conversation(user_message=request['input'], conversation_history=[])
    except Exception:
        result['native_error'] = True
    finally:
        guard.report_progress('native_returned')
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
                  generation_failure=guard.generation_failure,
                  wire_request_base64=base64.b64encode(guard.request_bytes).decode('ascii'),
                  wire_response_base64=base64.b64encode(guard.response_bytes).decode('ascii'))
    if binary_reasoning(policy):
        result.update(reasoning_catalog_base64=base64.b64encode(guard.reasoning_catalog_bytes).decode('ascii'),
                      reasoning_preflight_passed=guard.reasoning_preflight_passed and not guard.reasoning_preflight_failed)
    if compatibility_reasoning(policy):
        result['reasoning_request_evidence'] = reasoning_request_evidence(policy)
    progress.close()
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
