"""Bounded, untrusted diagnostic hints across a worker-only datagram capability.

Nothing in this module establishes completion or authorizes a runtime action.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time

MAX_PACKET_BYTES = 1024
MAX_PROGRESS_BYTES = 2048
MAX_PACKETS = 64
_MAX_READ_UPDATES = 40
_BINDINGS = ('request_sha256', 'runtime_policy_sha256', 'bridge_sha256')
_STAGES = frozenset({'setup', 'metadata_send', 'metadata_read', 'agent_ready',
                     'send', 'read', 'http_status', 'parse', 'guard_returned', 'native_returned'})
_COUNTERS = {'sequence': MAX_PACKETS, 'elapsed_ms': 720000, 'generation_requests': 1,
             'request_bytes': 512 * 1024, 'response_bytes_observed': 2**63 - 1,
             'response_bytes_retained': 4 * 1024 * 1024}
_FIELDS = set(_BINDINGS) | set(_COUNTERS) | {'schema_version', 'stage', 'http_status'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate progress field')
        result[key] = value
    return result


def parse_progress(data, bindings, max_elapsed_ms, previous=None):
    """Validate shape and binding, not the truth of a worker's claimed progress."""
    if not isinstance(data, bytes) or len(data) > MAX_PACKET_BYTES:
        raise ValueError('progress size')
    try:
        value = json.loads(data, object_pairs_hook=_unique)
    except (ValueError, RecursionError) as exc:
        raise ValueError('progress encoding') from exc
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError('progress fields')
    if (value['schema_version'] != 'hermes-native-progress/1.0'
            or type(value['stage']) is not str or value['stage'] not in _STAGES):
        raise ValueError('progress version or stage')
    for key in _BINDINGS:
        if (type(value[key]) is not str or re.fullmatch('[0-9a-f]{64}', value[key]) is None
                or value[key] != bindings[key]):
            raise ValueError('progress binding')
    for key, limit in _COUNTERS.items():
        if type(value[key]) is not int or not 0 <= value[key] <= limit:
            raise ValueError('progress counter')
    if not value['sequence'] or value['elapsed_ms'] > max_elapsed_ms:
        raise ValueError('progress bound')
    if value['response_bytes_retained'] > value['response_bytes_observed']:
        raise ValueError('progress byte counts')
    status = value['http_status']
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError('progress status')
    if previous is not None:
        if value['sequence'] <= previous['sequence']:
            raise ValueError('stale progress')
        if any(value[key] < previous[key] for key in _COUNTERS if key != 'sequence'):
            raise ValueError('regressing progress')
    return value


class ProgressWriter:
    """Best-effort bounded datagrams; never block or change generation behavior."""
    def __init__(self, fd, bindings, max_elapsed_ms):
        self.bindings = dict(bindings)
        self.max_elapsed_ms = max_elapsed_ms
        self.started = time.monotonic()
        self.sent = self.read_updates = 0
        self.last_read = None
        self.last_read_bytes = 0
        self.socket = None
        if type(fd) is int and fd >= 3 and os.name == 'posix':
            try:
                connection = socket.socket(fileno=fd)
                if connection.family != socket.AF_UNIX or connection.type != socket.SOCK_DGRAM:
                    connection.close()
                else:
                    connection.setblocking(False)
                    self.socket = connection
            except OSError:
                pass

    def emit(self, stage, *, generation_requests=0, request_bytes=0,
             response_bytes_observed=0, response_bytes_retained=0, http_status=None):
        if self.socket is None or self.sent >= MAX_PACKETS:
            return
        elapsed = min(self.max_elapsed_ms, max(0, int((time.monotonic() - self.started) * 1000)))
        if stage == 'read':
            if self.read_updates >= _MAX_READ_UPDATES or (self.last_read is not None
                    and elapsed - self.last_read < max(1, self.max_elapsed_ms // _MAX_READ_UPDATES)
                    and not (self.last_read_bytes == 0 and response_bytes_observed > 0)):
                return
            self.read_updates += 1
            self.last_read = elapsed
            self.last_read_bytes = response_bytes_observed
        value = {**self.bindings, 'schema_version': 'hermes-native-progress/1.0',
                 'sequence': self.sent + 1, 'stage': stage, 'elapsed_ms': elapsed,
                 'generation_requests': generation_requests, 'request_bytes': request_bytes,
                 'response_bytes_observed': response_bytes_observed,
                 'response_bytes_retained': response_bytes_retained, 'http_status': http_status}
        # Only caller-independent, finite fields can leave this channel.
        try:
            data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            parse_progress(data, self.bindings, self.max_elapsed_ms)
            self.sent += 1
            self.socket.send(data)
        except (OSError, ValueError, TypeError):
            return

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class ProgressChannel:
    """Parent-held hints survive child termination, with fixed packet/storage caps."""
    def __init__(self, bindings, max_elapsed_ms):
        self.bindings, self.max_elapsed_ms = dict(bindings), max_elapsed_ms
        self.reader = self.writer = None
        try:
            self.reader, self.writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.reader.settimeout(.02)
        except OSError:
            for connection in (self.reader, self.writer):
                if connection is not None:
                    connection.close()
            self.reader = self.writer = None
        self.stopping = threading.Event()
        self.last = None
        self.received = 0
        self.status = 'unavailable'
        self.thread = threading.Thread(target=self._receive, daemon=True)

    @property
    def child_fd(self):
        return self.writer.fileno() if self.writer is not None else None

    def __enter__(self):
        if self.reader is not None:
            try:
                self.thread.start()
            except RuntimeError:
                self.reader.close()
                self.close_parent_writer()
        return self

    def close_parent_writer(self):
        if self.writer is not None:
            self.writer.close()
            self.writer = None

    def _receive(self):
        try:
            while True:
                try:
                    data = self.reader.recv(MAX_PACKET_BYTES + 1)
                except socket.timeout:
                    if self.stopping.is_set():
                        return
                    continue
                except OSError:
                    return
                self.received += 1
                try:
                    if self.received > MAX_PACKETS:
                        raise ValueError('packet budget')
                    self.last = parse_progress(data, self.bindings, self.max_elapsed_ms, self.last)
                    self.status = 'observed'
                except (ValueError, TypeError):
                    self.status, self.last = 'rejected', None
                    return
        finally:
            self.reader.close()

    def __exit__(self, *args):
        self.close_parent_writer()
        self.stopping.set()
        if self.thread.ident is None:
            return
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            self.reader.close()
            self.status, self.last = 'unavailable', None

    def summary(self):
        return {'schema_version': 'hermes-native-progress-summary/1.0',
                'source': 'untrusted_worker_progress', 'authoritative': False,
                'server_completion_verified': False, 'status': self.status,
                'packets_received': min(self.received, MAX_PACKETS + 1), 'last': self.last}
