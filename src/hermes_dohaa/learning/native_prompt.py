"""Isolated native-Hermes prompt adapter for a privately pinned LM Studio backend.

Linux development boundary: a privileged collector starts fresh workers under a
separate unprivileged account. No oracle bytes or paths enter worker requests.
An exclusively reserved backend and immutable trusted installations are required.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from types import MappingProxyType
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import collection, shadow
from .native_progress import MAX_PROGRESS_BYTES, ProgressChannel
from .native_response_format import response_format_for_policy

MAX_WIRE = 512 * 1024
# SSE repeats framing for each delta; its response needs a separate bound from
# requests and metadata. Base64 of both maximum bodies fits an 8 MiB trace.
MAX_RESPONSE_WIRE = 4 * 1024 * 1024
MAX_WORKER_TRACE = 8 * 1024 * 1024
MAX_TRACE = 64 * MAX_WORKER_TRACE
_REASONS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}
FAILURE_STAGES = frozenset({'send', 'read', 'close', 'http_status', 'parse'})
FAILURE_CODES = frozenset('native_shadow.' + name for name in (
    'transport_error', 'transport_timeout', 'wire_limit', 'http_status',
    'response_invalid', 'response_incomplete', 'response_identity', 'response_choices',
    'response_tools', 'response_role', 'response_content', 'response_limit'))


class NativePromptError(RuntimeError):
    def __init__(self, code='native_shadow.invalid'):
        self.code = code
        super().__init__(code)


def worker_failure_code(trace):
    """Read finite diagnostic metadata, never an arbitrary worker error message.

    This is diagnostic evidence only: it cannot establish server completion or
    authorize cleanup. Callers must separately verify worker/request bindings.
    """
    failure = trace.get('generation_failure')
    fallback = 'native_shadow.worker_unverified'
    if type(failure) is not dict or set(failure) != {
            'code', 'stage', 'request_bytes', 'response_bytes_observed',
            'response_bytes_retained', 'http_status'}:
        return fallback
    if (type(failure['code']) is not str or failure['code'] not in FAILURE_CODES
            or type(failure['stage']) is not str or failure['stage'] not in FAILURE_STAGES):
        return fallback
    for field, limit in (('request_bytes', MAX_WIRE), ('response_bytes_observed', 2**63-1),
                         ('response_bytes_retained', MAX_RESPONSE_WIRE)):
        if type(failure[field]) is not int or not 0 <= failure[field] <= limit:
            return fallback
    status = failure['http_status']
    if ((status is not None and (type(status) is not int or not 100 <= status <= 599))
            or failure['response_bytes_retained'] > failure['response_bytes_observed']):
        return fallback
    return failure['code']


def native_adapter_sha256():
    return shadow._hash(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))


def native_bridge_sha256():
    paths = (Path(__file__), Path(__file__).with_name('native_worker.py'),
             Path(__file__).with_name('native_progress.py'),
             Path(__file__).with_name('native_response_format.py'))
    return shadow._hash(shadow._canonical({p.name: shadow._hash(p.read_bytes().replace(b'\r\n', b'\n'))
                                          for p in paths}))


def validate_native_policy(data: bytes, expected_sha256: str):
    policy = shadow._json(data, expected_sha256)
    fields = {'schema_version', 'native_commit', 'bridge_sha256', 'model', 'endpoint',
        'reasoning_effort', 'seed', 'temperature', 'top_p', 'max_tokens', 'request_timeout_seconds',
        'worker_timeout_seconds', 'request_limit', 'worker_uid', 'worker_gid', 'exclusive_backend'}
    if policy.get('schema_version') == 'hermes-native-shadow-policy/1.1':
        fields.add('response_contract')
    shadow._fields(policy, fields)
    try:
        response_format_for_policy(policy)
    except ValueError as exc:
        raise NativePromptError('native_shadow.policy_invalid') from exc
    if (policy['bridge_sha256'] != native_bridge_sha256()
            or policy['exclusive_backend'] is not True
            or type(policy['native_commit']) is not str
            or re.fullmatch('[0-9a-f]{40}', policy['native_commit']) is None
            or type(policy['model']) is not str or not 1 <= len(policy['model']) <= 256
            or type(policy['reasoning_effort']) is not str or policy['reasoning_effort'] not in _REASONS):
        raise NativePromptError('native_shadow.policy_invalid')
    for name, low, high in (('seed', 0, 2**31 - 1), ('max_tokens', 1, 16384),
            ('request_timeout_seconds', 1, 600), ('worker_timeout_seconds', 2, 720),
            ('request_limit', 2, 512), ('worker_uid', 1, 2**31-1), ('worker_gid', 1, 2**31-1)):
        if type(policy[name]) is not int or not low <= policy[name] <= high:
            raise NativePromptError('native_shadow.policy_invalid')
    for name, low, high in (('temperature', 0, 2), ('top_p', 0, 1)):
        if type(policy[name]) not in (int, float) or not low <= policy[name] <= high:
            raise NativePromptError('native_shadow.policy_invalid')
    if not policy['top_p'] or policy['worker_timeout_seconds'] <= policy['request_timeout_seconds']:
        raise NativePromptError('native_shadow.policy_invalid')
    try:
        url = urlsplit(policy['endpoint'])
        ipaddress.ip_address(url.hostname)
        if (url.scheme not in {'http', 'https'} or url.username or url.password or url.query
                or url.fragment or url.path != '/v1' or not url.port):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise NativePromptError('native_shadow.endpoint_invalid') from exc
    return policy


def prompt_frame(request):
    identifier = request['request_id']
    return f'HERMES_SHADOW_BEGIN_{identifier}\n{request["prompt"]}\nHERMES_SHADOW_END_{identifier}'


def validate_request(data: bytes, policy_sha256: str):
    request = shadow._json(data, shadow._hash(data))
    shadow._fields(request, {'schema_version', 'request_id', 'input', 'prompt', 'input_sha256',
                             'prompt_sha256', 'execution_policy_sha256'})
    if request['schema_version'] != 'hermes-shadow-request/1.0' or request['execution_policy_sha256'] != policy_sha256:
        raise NativePromptError('native_shadow.request_invalid')
    shadow._digest(request['request_id'])
    for name in ('input', 'prompt'):
        value = collection._text(request[name])
        if shadow._hash(value.encode('utf-8')) != request[name + '_sha256']:
            raise NativePromptError('native_shadow.request_invalid')
    return request


def validate_wire_request(data: bytes, logical: dict[str, Any], policy: dict[str, Any]):
    if len(data) > MAX_WIRE:
        raise NativePromptError('native_shadow.wire_limit')
    raw = shadow._json(data, shadow._hash(data))
    expected = {name: policy[name] for name in ('model', 'reasoning_effort', 'seed',
                                              'temperature', 'top_p', 'max_tokens')}
    response_format = response_format_for_policy(policy)
    if response_format is not None:
        expected['response_format'] = response_format
    allowed = set(expected) | {'messages', 'stream', 'stream_options', 'tools', 'tool_choice', 'think'}
    if (any(name not in raw or not shadow._equal(raw[name], value) for name, value in expected.items())
            or set(raw) - allowed or type(raw.get('stream')) is not bool
            or ('stream_options' in raw and raw['stream_options'] != {'include_usage': True})
            or ('think' in raw and (type(raw['think']) is not bool
                                    or raw['think'] != (policy['reasoning_effort'] != 'none')))
            or raw.get('tools') or raw.get('functions') or raw.get('tool_choice') not in (None, 'none')
            or raw.get('function_call') not in (None, 'none')):
        raise NativePromptError('native_shadow.wire_parameters')
    messages = raw.get('messages')
    if type(messages) is not list or not messages:
        raise NativePromptError('native_shadow.wire_messages')
    systems, users = [], []
    for message in messages:
        if (type(message) is not dict or type(message.get('content')) is not str
                or message.get('role') not in {'system', 'user'}):
            raise NativePromptError('native_shadow.wire_messages')
        (systems if message['role'] == 'system' else users).append(message['content'])
    if users != [logical['input']] or '\n'.join(systems).count(prompt_frame(logical)) != 1:
        raise NativePromptError('native_shadow.wire_messages')
    return raw


def parse_wire_response(data: bytes, model: str):
    """Require a complete single-choice OpenAI JSON/SSE response, never a prefix."""
    if type(data) is not bytes or len(data) > MAX_RESPONSE_WIRE:
        raise NativePromptError('native_shadow.wire_limit')
    stream = data.lstrip().startswith(b'data:')
    documents = []
    if stream:
        done = False
        for line in data.splitlines():
            if not line.strip():
                continue
            if not line.startswith(b'data:') or done:
                raise NativePromptError('native_shadow.response_incomplete')
            payload = line[5:].strip()
            if payload == b'[DONE]':
                done = True
            else:
                documents.append(shadow._json(payload, shadow._hash(payload)))
        if not done:
            raise NativePromptError('native_shadow.response_incomplete')
    else:
        documents = [shadow._json(data, shadow._hash(data))]
    content, reasoning, finishes = [], 0, []
    for raw in documents:
        if raw.get('model') != model or type(raw.get('choices')) is not list:
            raise NativePromptError('native_shadow.response_identity')
        choices = raw['choices']
        if len(choices) > 1 or (not stream and len(choices) != 1):
            raise NativePromptError('native_shadow.response_choices')
        for choice in choices:
            if type(choice) is not dict or type(choice.get('index')) is not int or choice['index'] != 0:
                raise NativePromptError('native_shadow.response_choices')
            message = choice.get('delta' if stream else 'message')
            if type(message) is not dict or message.get('tool_calls') or message.get('function_call'):
                raise NativePromptError('native_shadow.response_tools')
            if message.get('role') not in (None, 'assistant') or (not stream and message.get('role') != 'assistant'):
                raise NativePromptError('native_shadow.response_role')
            value = message.get('content')
            if value is not None and type(value) is not str:
                raise NativePromptError('native_shadow.response_content')
            if finishes and value:
                raise NativePromptError('native_shadow.response_incomplete')
            content.append(value or '')
            for field in ('reasoning', 'reasoning_content'):
                value = message.get(field)
                if value is not None and type(value) is not str:
                    raise NativePromptError('native_shadow.response_content')
                reasoning += len(value or '')
            finish = choice.get('finish_reason')
            if finish is not None:
                if finish not in {'stop', 'length'} or finishes:
                    raise NativePromptError('native_shadow.response_incomplete')
                finishes.append(finish)
    if len(finishes) != 1:
        raise NativePromptError('native_shadow.response_incomplete')
    text = ''.join(content)
    if len(text.encode('utf-8')) > collection.MAX_TEXT_BYTES:
        raise NativePromptError('native_shadow.response_limit')
    return {'content': text, 'finish_reason': finishes[0], 'reasoning_characters': reasoning}


class NativePromptAdapter:
    """One native worker per request; credentials never enter argv, env or reports."""

    def __init__(self, *, policy_bytes: bytes, expected_policy_sha256: str,
                 collection_policy_bytes: bytes, native_source: Path, python: Path,
                 evidence_dir: Path, api_key: str):
        self._policy = MappingProxyType(validate_native_policy(policy_bytes, expected_policy_sha256))
        self.runtime_policy_sha256 = expected_policy_sha256
        self.collection_sha256 = shadow._hash(collection_policy_bytes)
        cp = shadow._json(collection_policy_bytes, self.collection_sha256)
        expected = collection.create_collection_policy(adapter_sha256=native_adapter_sha256(),
            runtime_policy_sha256=expected_policy_sha256, result_fields=cp.get('result_fields'))
        if not shadow._equal(cp, expected):
            raise NativePromptError('native_shadow.collection_policy_mismatch')
        self.native_source, self.python = Path(native_source).resolve(), Path(python).absolute()
        self.evidence_dir, self._api_key = Path(evidence_dir), api_key
        self.state, self.calls, self.trace_bytes, self.owned = 'new', 0, 0, set()
        self.worker_root = None

    @property
    def policy(self):
        return self._policy

    def _catalog(self):
        url = urlsplit(self.policy['endpoint'])
        catalog = self._http(f'{url.scheme}://{url.netloc}/api/v1/models')
        if type(catalog) is not dict or type(catalog.get('models')) is not list:
            raise NativePromptError('native_shadow.catalog_invalid')
        loaded, seen = {}, set()
        for model in catalog['models']:
            if type(model) is not dict or type(model.get('key')) is not str or model['key'] in seen:
                raise NativePromptError('native_shadow.catalog_invalid')
            seen.add(model['key'])
            if model.get('type') not in {'llm', 'vlm'}:
                continue
            instances = model.get('loaded_instances', [])
            if type(instances) is not list:
                raise NativePromptError('native_shadow.catalog_invalid')
            for instance in instances:
                if type(instance) is not dict or type(instance.get('id')) is not str or not instance['id']:
                    raise NativePromptError('native_shadow.catalog_invalid')
                identifier = instance['id']
                if identifier in loaded:
                    raise NativePromptError('native_shadow.catalog_invalid')
                loaded[identifier] = model['key']
        if self.policy['model'] not in seen:
            raise NativePromptError('native_shadow.model_unavailable')
        return loaded

    def _http(self, url, body=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._api_key}
        request = urllib.request.Request(url, data=None if body is None else shadow._canonical(body), headers=headers)
        # Explicit no-proxy opener; credentials must never follow redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise NativePromptError('native_shadow.redirect_forbidden')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=self.policy['request_timeout_seconds']) as response:
            data = response.read(MAX_WIRE + 1)
        if len(data) > MAX_WIRE:
            raise NativePromptError('native_shadow.catalog_limit')
        return shadow._json(data, shadow._hash(data))

    def start(self):
        if os.name != 'posix' or not hasattr(os, 'geteuid') or os.geteuid() != 0 or self.state != 'new':
            raise NativePromptError('native_shadow.isolation_unavailable')
        head = subprocess.check_output(['git', '-C', str(self.native_source), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(self.native_source), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
        if head != self.policy['native_commit'] or dirty or self._catalog():
            raise NativePromptError('native_shadow.precondition_failed')
        self.evidence_dir.mkdir(mode=0o700, exist_ok=False)
        self.worker_root = Path(tempfile.mkdtemp(prefix='hermes-native-shadow-'))
        # Python's import finder must list this code-only directory. Profiles
        # below it remain private; oracle/evidence directories stay elsewhere.
        os.chmod(self.worker_root, 0o755)
        package = Path(__file__).resolve().parents[1]
        target = self.worker_root / 'hermes_dohaa'
        shutil.copytree(package, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for path in target.rglob('*'):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(target, 0o555)
        self.state = 'idle'
        return shadow._canonical(collection._LIFECYCLE)

    def generate(self, request_bytes: bytes):
        request = validate_request(request_bytes, self.collection_sha256)
        if self.state != 'idle' or self.calls >= self.policy['request_limit']:
            raise NativePromptError('native_shadow.not_ready')
        current = self._catalog()
        if set(current) != self.owned or any(model != self.policy['model'] for model in current.values()):
            raise NativePromptError('native_shadow.residency_changed')
        if self.trace_bytes + MAX_WORKER_TRACE + MAX_PROGRESS_BYTES > MAX_TRACE:
            raise NativePromptError('native_shadow.trace_budget')
        profile = self.worker_root / ('profile-' + str(self.calls))
        profile.mkdir(mode=0o700)
        os.chown(profile, self.policy['worker_uid'], self.policy['worker_gid'])
        receipt_path = self.evidence_dir / f'worker-{self.calls:04d}.json'
        log_path = self.evidence_dir / f'worker-{self.calls:04d}.private.log'
        configuration = {'policy': dict(self.policy), 'runtime_policy_sha256': self.runtime_policy_sha256,
                         'collection_policy_sha256': self.collection_sha256,
                         'request': request, 'request_sha256': shadow._hash(request_bytes),
                         'profile': str(profile), 'api_key': self._api_key}
        environment = {'PATH': os.defpath, 'HOME': str(profile), 'HERMES_HOME': str(profile),
            'PYTHONPATH': os.pathsep.join((str(self.worker_root), str(self.native_source))),
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8',
            'PYTHONNOUSERSITE': '1',
            'HERMES_DISABLE_TELEMETRY': '1'}
        self.state = 'uncertain'
        self.calls += 1
        progress = ProgressChannel(
            {'request_sha256': configuration['request_sha256'],
             'runtime_policy_sha256': self.runtime_policy_sha256,
             'bridge_sha256': self.policy['bridge_sha256']},
            self.policy['worker_timeout_seconds'] * 1000)
        try:
            with progress, receipt_path.open('xb') as out, log_path.open('xb') as log:
                configuration['progress_fd'] = progress.child_fd
                os.fchmod(out.fileno(), 0o600)
                os.fchmod(log.fileno(), 0o600)
                process = subprocess.Popen([str(self.python), '-P', '-m', 'hermes_dohaa.learning.native_worker'],
                    stdin=subprocess.PIPE, stdout=out, stderr=log, cwd=profile, env=environment,
                    user=self.policy['worker_uid'], group=self.policy['worker_gid'], extra_groups=[],
                    umask=0o077, close_fds=True, start_new_session=True,
                    pass_fds=(progress.child_fd,) if progress.child_fd is not None else ())
                progress.close_parent_writer()
                try:
                    process.communicate(shadow._canonical(configuration), timeout=self.policy['worker_timeout_seconds'])
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    process.kill()
                    process.wait(timeout=10)
                    raise NativePromptError('native_shadow.worker_timeout') from None
                out.flush()
                os.fsync(out.fileno())
        finally:
            # Diagnostic hints never substitute for the terminal trace below.
            # A failed diagnostic write cannot change a generation's verdict.
            self.last_progress = progress.summary()
            try:
                raw_progress = shadow._canonical(self.last_progress)
                if len(raw_progress) <= MAX_PROGRESS_BYTES:
                    shadow._publish(self.evidence_dir / f'worker-{self.calls - 1:04d}.progress', raw_progress)
                    self.trace_bytes += len(raw_progress)
            except Exception:
                self.last_progress = {'status': 'storage_unavailable', 'authoritative': False,
                                      'server_completion_verified': False}
            # The code-only parent is root-owned and not worker-writable, so the
            # worker cannot replace this directory entry. Seal its history from
            # subsequent workers without following or modifying its child links.
            os.chown(profile, 0, 0, follow_symlinks=False)
            os.chmod(profile, 0o700, follow_symlinks=False)
        data = shadow._read(receipt_path)
        self.trace_bytes += len(data)
        trace = shadow._json(data, shadow._hash(data))
        if (process.returncode or trace.get('request_sha256') != shadow._hash(request_bytes)
                or trace.get('uid') != self.policy['worker_uid'] or trace.get('gid') != self.policy['worker_gid']
                or trace.get('identity_isolated') is not True or trace.get('agent_class') != 'AIAgent'
                or trace.get('runtime_policy_sha256') != self.runtime_policy_sha256
                or trace.get('bridge_sha256') != self.policy['bridge_sha256']
                or trace.get('profile_passed') is not True):
            raise NativePromptError('native_shadow.worker_unverified')
        if trace.get('server_finished') is not True:
            raise NativePromptError(worker_failure_code(trace))
        if trace.get('generation_failure') is not None:
            raise NativePromptError('native_shadow.worker_unverified')
        wire_request = base64.b64decode(trace['wire_request_base64'], validate=True)
        wire_response = base64.b64decode(trace['wire_response_base64'], validate=True)
        validate_wire_request(wire_request, request, self.policy)
        parsed = parse_wire_response(wire_response, self.policy['model'])
        if self.policy['reasoning_effort'] == 'none' and parsed['reasoning_characters']:
            raise NativePromptError('native_shadow.reasoning_mismatch')
        if type(trace.get('actual_requests')) is not int or trace['actual_requests'] != 1:
            raise NativePromptError('native_shadow.native_result_unverified')
        budget_stopped = parsed['finish_reason'] == 'length' or bool(trace.get('denied_continuations'))
        if not budget_stopped and trace.get('native_completed') is True and trace.get('content_matches_wire') is not True:
            raise NativePromptError('native_shadow.native_result_unverified')
        current = self._catalog()
        if any(model != self.policy['model'] for model in current.values()):
            raise NativePromptError('native_shadow.residency_changed')
        self.owned = set(current)
        self.state = 'idle'
        receipt = {'schema_version': 'hermes-shadow-terminal/1.0',
                   'request_sha256': shadow._hash(request_bytes), 'server_finished': True}
        if budget_stopped:
            return shadow._canonical(dict(receipt, status='failed', error_code='budget_exhausted'))
        if trace.get('native_completed') is not True:
            return shadow._canonical(dict(receipt, status='failed', error_code='runtime_error'))
        return shadow._canonical(dict(receipt, status='completed', content=parsed['content']))

    def finish(self):
        if self.state != 'idle':
            raise NativePromptError('native_shadow.completion_unknown')
        current = self._catalog()
        if set(current) != self.owned or any(model != self.policy['model'] for model in current.values()):
            raise NativePromptError('native_shadow.residency_changed')
        self.state = 'closing'
        url = urlsplit(self.policy['endpoint'])
        for identifier in sorted(self.owned):
            self._http(f'{url.scheme}://{url.netloc}/api/v1/models/unload', {'instance_id': identifier})
        for _ in range(30):
            if not self._catalog():
                self.owned.clear()
                self.state = 'closed'
                return shadow._canonical(collection._LIFECYCLE)
            time.sleep(1)
        raise NativePromptError('native_shadow.unload_unverified')
