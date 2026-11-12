"""Both applications retain their same-origin boundary after gateway integration."""
import httpx
from fastapi.testclient import TestClient

from ceres_lab.gateway import Config, create_app


def test_proxy_retargets_host_and_validated_origin(tmp_path):
    received = []

    def upstream(request):
        received.append(request)
        assert request.headers['host'] == '127.0.0.1:4188'
        assert request.headers.get('origin') == 'http://127.0.0.1:4188'
        assert request.url.query == b'limit=3'
        class ResponseBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield request.content
        return httpx.Response(200, stream=ResponseBody(), headers={'Content-Type': 'application/json'})

    app = create_app(Config(runtime=tmp_path, background=False,
                            archive_url='http://127.0.0.1:4188', extra_hosts={'testserver'}))
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            response = client.post('/api/jobs?limit=3', json={'job': 'example'},
                                   headers={'Origin': 'http://testserver'})
            assert response.status_code == 200
            assert len(received) == 1
            for headers in [{'Origin': 'http://evil.invalid'}, {'Origin': 'null'},
                            {'Origin': 'https://testserver'}, {'Sec-Fetch-Site': 'cross-site'},
                            [('Origin', 'http://testserver'), ('Origin', 'http://evil.invalid')]]:
                for method in ('GET', 'POST'):
                    assert client.request(method, '/api/jobs', headers=headers).status_code == 403
            assert len(received) == 1
        finally:
            client.portal.call(app.state.http.aclose)
            app.state.http = original
