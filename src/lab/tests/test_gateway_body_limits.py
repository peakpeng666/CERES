"""ASGI body limits: real chunks, parser cleanup and exception propagation, no server."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from urp_lab import gateway as shipped_gateway


@pytest.fixture
def gateway(monkeypatch):
    module = shipped_gateway
    monkeypatch.setattr(module, 'MAX_BODY', 8)
    monkeypatch.setattr(module, 'MAX_UPLOAD', 32)
    return module


class Exchange:
    def __init__(self, chunks=(), *, method='POST', path='/lab/api/test', headers=(), messages=None):
        self.scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                      'scheme': 'http', 'method': method, 'path': path, 'raw_path': path.encode(),
                      'root_path': '', 'query_string': b'', 'server': ('127.0.0.1', 4100),
                      'client': ('127.0.0.1', 12345),
                      'headers': [(b'host', b'127.0.0.1:4100'), *headers]}
        if not any(k == b'content-type' for k, _ in headers):
            self.scope['headers'].append((b'content-type', b'application/json'))
        self.messages = list(messages) if messages is not None else [
            {'type': 'http.request', 'body': part, 'more_body': i < len(chunks) - 1}
            for i, part in enumerate(chunks)]
        self.received = 0
        self.sent = []

    async def receive(self):
        assert self.received < len(self.messages), 'The guard or app consumed beyond the request'
        message = self.messages[self.received]
        self.received += 1
        return message

    async def send(self, message):
        self.sent.append(message)

    def run(self, app):
        asyncio.run(app(self.scope, self.receive, self.send))
        return self

    @property
    def status(self):
        return next(m['status'] for m in self.sent if m['type'] == 'http.response.start')

    @property
    def result(self):
        return json.loads(b''.join(m.get('body', b'') for m in self.sent if m['type'] == 'http.response.body'))


async def consume(scope, receive, send):
    size = 0
    while True:
        message = await receive()
        if message['type'] == 'http.disconnect':
            break
        size += len(message.get('body', b''))
        if not message.get('more_body', False):
            break
    await shipped_gateway.JSONResponse({'bytes': size})(scope, receive, send)


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'GET', 'HEAD'])
@pytest.mark.parametrize('headers', [[], [(b'transfer-encoding', b'chunked')]])
def test_chunked_limit_applies_to_every_body_reader(gateway, method, headers):
    exchange = Exchange([b'1234', b'5678', b'9', b'unread'], method=method,
                        path='/api/jobs', headers=headers).run(gateway.Guard(consume))
    assert exchange.status == 413
    assert exchange.result['error'] == 'payload_too_large'
    assert exchange.received == 3


@pytest.mark.parametrize('headers', [[], [(b'content-length', b'8')], [(b'content-length', b'0008')]])
def test_exact_limit_empty_chunks_and_final_empty_chunk(gateway, headers):
    exchange = Exchange([b'', b'1234', b'5678', b''], headers=headers).run(gateway.Guard(consume))
    assert exchange.status == 200 and exchange.result == {'bytes': 8}
    assert exchange.received == 4


@pytest.mark.parametrize('length', [b'9', b'9' * 5000])
def test_oversized_declaration_is_rejected_before_consuming(gateway, length):
    exchange = Exchange([b'unread'], headers=[(b'content-length', length)]).run(gateway.Guard(consume))
    assert exchange.status == 413 and exchange.received == 0


@pytest.mark.parametrize('length', [b'', b'-1', b'+1', b'1.0', b'1,1', b'1x', b'\xb2'])
def test_invalid_content_length_is_400_not_500(gateway, length):
    exchange = Exchange([b'unread'], headers=[(b'content-length', length)]).run(gateway.Guard(consume))
    assert exchange.status == 400 and exchange.result['error'] == 'invalid_content_length'
    assert exchange.received == 0


@pytest.mark.parametrize('headers', [
    [(b'content-length', b'1'), (b'content-length', b'1')],
    [(b'content-length', b'1'), (b'transfer-encoding', b'chunked')],
])
def test_ambiguous_framing_is_rejected(gateway, headers):
    exchange = Exchange([b'unread'], headers=headers).run(gateway.Guard(consume))
    assert exchange.status == 400 and exchange.received == 0


@pytest.mark.parametrize('length,chunks', [(b'1', [b'a', b'bc']), (b'7', [b'ab', b'cd']), (b'0', [b'a'])])
def test_understated_and_truncated_lengths_are_rejected(gateway, length, chunks):
    exchange = Exchange(chunks, headers=[(b'content-length', length)]).run(gateway.Guard(consume))
    assert exchange.status == 400 and exchange.result['error'] == 'invalid_content_length'


def test_actual_limit_does_not_trust_an_understated_header(gateway):
    exchange = Exchange([b'123456789'], headers=[(b'content-length', b'1')]).run(gateway.Guard(consume))
    assert exchange.status == 413 and exchange.result['error'] == 'payload_too_large'


def test_long_zero_prefix_has_no_integer_conversion_limit(gateway):
    exchange = Exchange([b'12345678'], headers=[(b'content-length', b'0' * 5000 + b'8')]).run(gateway.Guard(consume))
    assert exchange.status == 200


def test_upload_remains_lazy_and_receives_the_original_chunks(gateway):
    exchange = Exchange([b'x' * 16, b'y' * 16], path='/lab/api/assets/inspect', headers=[
        (b'content-type', b'multipart/form-data; boundary=x'), (b'x-workbench-upload', b'1')])

    async def reader(scope, receive, send):
        assert exchange.received == 0
        assert await receive() is exchange.messages[0]
        assert exchange.received == 1
        assert await receive() is exchange.messages[1]
        await gateway.JSONResponse({'streamed': True})(scope, receive, send)

    exchange.run(gateway.Guard(reader))
    assert exchange.status == 200 and exchange.result == {'streamed': True}


@pytest.mark.parametrize('path', ['/lab/api/assets/inspect', '/lab/api/assets/asset-1/replace'])
def test_upload_uses_its_larger_limit_but_still_counts(gateway, path):
    exchange = Exchange([b'x' * 16, b'y' * 16, b'z'], path=path, headers=[
        (b'content-type', b'multipart/form-data; boundary=x'), (b'x-workbench-upload', b'1')]).run(gateway.Guard(consume))
    assert exchange.status == 413 and exchange.received == 3


def test_upload_header_does_not_enlarge_other_endpoints(gateway):
    exchange = Exchange([b'123456789'], path='/api/jobs', headers=[
        (b'content-type', b'multipart/form-data; boundary=x'), (b'x-workbench-upload', b'1')]).run(gateway.Guard(consume))
    assert exchange.status == 413


def test_failure_is_latched_without_reading_more(gateway):
    exchange = Exchange([b'123456789', b'unread'])

    async def reader(scope, receive, send):
        with pytest.raises(gateway._RequestBodyError) as first:
            await receive()
        with pytest.raises(gateway._RequestBodyError) as second:
            await receive()
        assert first.value is second.value
        raise first.value

    exchange.run(gateway.Guard(reader))
    assert exchange.status == 413 and exchange.received == 1


def test_disconnect_passes_through_without_claiming_truncation(gateway):
    exchange = Exchange(headers=[(b'content-length', b'8')], messages=[{'type': 'http.disconnect'}])

    async def reader(scope, receive, send):
        assert await receive() is exchange.messages[0]

    exchange.run(gateway.Guard(reader))
    assert exchange.received == 1 and exchange.sent == []


def test_unrelated_downstream_exception_is_not_swallowed(gateway):
    failure = RuntimeError('downstream failure')

    async def reader(scope, receive, send):
        await receive()
        raise failure

    exchange = Exchange([b'{}'])
    with pytest.raises(RuntimeError) as caught:
        exchange.run(gateway.Guard(reader))
    assert caught.value is failure and exchange.sent == []


def test_started_response_does_not_get_a_second_status(gateway):
    async def reader(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await receive()

    exchange = Exchange([b'123456789'])
    with pytest.raises(gateway._RequestBodyError):
        exchange.run(gateway.Guard(reader))
    assert [m['type'] for m in exchange.sent] == ['http.response.start']


def test_non_http_scope_is_untouched(gateway):
    exchange = Exchange()
    exchange.scope = {'type': 'lifespan'}

    async def app(scope, receive, send):
        assert scope is exchange.scope and receive == exchange.receive and send == exchange.send

    exchange.run(gateway.Guard(app))


def gateway_without_runtime(gateway, monkeypatch):
    # Register the actual production routes and handlers without creating a
    # Lab runtime, starting lifespan services or opening any listening socket.
    monkeypatch.setattr(gateway, 'Lab', lambda config: SimpleNamespace())
    return gateway.create_app(gateway.Config(background=False))


def test_fastapi_json_parser_keeps_413_and_error_code(gateway, monkeypatch):
    app = gateway_without_runtime(gateway, monkeypatch)
    exchange = Exchange([b'{"text":', b'"oversize"}'], path='/lab/api/compose/parse').run(app)
    assert exchange.status == 413 and exchange.result['error'] == 'payload_too_large'


def test_archive_get_is_rejected_before_any_upstream_request(gateway, monkeypatch):
    app = gateway_without_runtime(gateway, monkeypatch)

    def unexpected(*args, **kwargs):
        pytest.fail('Oversized archive request must not reach its upstream')

    app.state.http = SimpleNamespace(build_request=unexpected)
    exchange = Exchange([b'12345678', b'9'], method='GET', path='/api/jobs').run(app)
    assert exchange.status == 413


def test_multipart_overflow_closes_spooled_files(gateway, monkeypatch):
    from starlette import formparsers
    files = []
    original = formparsers.SpooledTemporaryFile

    def track(*args, **kwargs):
        file = original(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', track)
    monkeypatch.setattr(formparsers.MultiPartParser, 'spool_max_size', 16)
    header = (b'--x\r\nContent-Disposition: form-data; name="files"; filename="x.bin"\r\n'
              b'Content-Type: application/octet-stream\r\n\r\n')
    monkeypatch.setattr(gateway, 'MAX_UPLOAD', len(header) + 150)
    app = gateway_without_runtime(gateway, monkeypatch)
    exchange = Exchange([header, b'a' * 100, b'b' * 100, b'\r\n--x--\r\n'],
                        path='/lab/api/assets/inspect', headers=[
                            (b'content-type', b'multipart/form-data; boundary=x'),
                            (b'x-workbench-upload', b'1')]).run(app)
    assert exchange.status == 413 and exchange.result['error'] == 'payload_too_large'
    assert exchange.received == 3
    assert len(files) == 1 and files[0]._rolled and files[0].closed
