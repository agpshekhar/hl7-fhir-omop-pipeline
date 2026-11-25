"""Tests for the deploy job, against a fake BridgeLink admin API (plain HTTP on localhost)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from labpipe.bridgelink.deploy import (
    Engine,
    EngineError,
    channel_file,
    channel_id,
    deploy_channel,
    wait_until_ready,
)
from labpipe.cli import main

ROOT = Path(__file__).resolve().parent.parent
CHANNEL_XML = (ROOT / "interfaces" / "bridgelink" / "lab-results-in.xml").read_text()
CID = channel_id(CHANNEL_XML)


class FakeAdminApi:
    """Just enough of the REST API: version, get/create/update a channel, deploy, status."""

    def __init__(self, password="admin"):
        self.channels: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body=""):
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                api.calls.append((self.command, self.path))
                if self.headers.get("X-Requested-With") is None:
                    return self._reply(400, "missing X-Requested-With")
                if self.headers.get("Authorization") != Engine("", "admin", password).auth_header:
                    return self._reply(401, "bad credentials")
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                path = self.path.split("?")[0]
                if path == "/api/server/version":
                    return self._reply(200, "26.9.0")
                if self.command == "POST" and path == "/api/channels":
                    api.channels[channel_id(body)] = body
                    return self._reply(200, "<boolean>true</boolean>")
                if path == f"/api/channels/{CID}":
                    if self.command == "GET":
                        return self._reply(200, api.channels.get(CID, ""))
                    if self.command == "PUT":
                        api.channels[CID] = body
                        return self._reply(200, "<boolean>true</boolean>")
                if self.command == "POST" and path == f"/api/channels/{CID}/_deploy":
                    return self._reply(204)
                if path == f"/api/channels/{CID}/status":
                    return self._reply(
                        200, "<dashboardStatus><state>STARTED</state></dashboardStatus>"
                    )
                return self._reply(404)

            do_GET = do_POST = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def api():
    fake = FakeAdminApi()
    yield fake
    fake.server.shutdown()


def test_first_deploy_creates_the_channel_and_starts_it(api):
    engine = Engine(api.url, "admin", "admin")
    assert deploy_channel(engine, CHANNEL_XML) == "STARTED"
    assert api.channels == {CID: CHANNEL_XML}
    assert ("POST", "/api/channels") in api.calls
    assert ("POST", f"/api/channels/{CID}/_deploy?returnErrors=true") in api.calls


def test_second_deploy_overwrites_the_same_channel(api):
    engine = Engine(api.url, "admin", "admin")
    deploy_channel(engine, CHANNEL_XML)
    api.calls.clear()
    assert deploy_channel(engine, CHANNEL_XML) == "STARTED"
    assert ("PUT", f"/api/channels/{CID}?override=true") in api.calls
    assert ("POST", "/api/channels") not in api.calls
    assert len(api.channels) == 1


def test_wait_until_ready_returns_the_version(api):
    assert wait_until_ready(Engine(api.url, "admin", "admin")) == "26.9.0"


def test_a_wrong_password_fails_at_once_instead_of_waiting(api):
    with pytest.raises(EngineError, match="401"):
        wait_until_ready(Engine(api.url, "admin", "wrong"), timeout=60)
    assert len(api.calls) == 1


def test_an_engine_that_never_answers_times_out():
    with pytest.raises(EngineError, match="not ready"):
        wait_until_ready(Engine("http://127.0.0.1:9", "admin", "admin"), timeout=0.2, interval=0.05)


def test_channel_id_refuses_xml_without_an_id():
    with pytest.raises(EngineError):
        channel_id("<channel><name>x</name></channel>")


def test_channel_file_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LABPIPE_CHANNEL_FILE", str(tmp_path / "c.xml"))
    assert channel_file() == tmp_path / "c.xml"


def test_bridgelink_deploy_cli_reports_the_started_channel(api, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGELINK_URL", api.url)
    assert main(["bridgelink", "deploy"]) == 0
    assert (
        "bridgelink 26.9.0: deployed lab-results-in.xml, channel STARTED" in capsys.readouterr().out
    )
