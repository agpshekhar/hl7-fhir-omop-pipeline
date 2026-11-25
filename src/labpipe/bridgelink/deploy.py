"""Load the committed channel into BridgeLink and deploy it, through its REST API.

BridgeLink keeps its channels in its own database, inside a Docker volume. If the
channel only lived there, a fresh clone (or CI) would start an engine with no channel.
So the channel is a file in the repo, interfaces/bridgelink/lab-results-in.xml, and
this job pushes it to the engine on every `docker compose up`: create it the first
time, overwrite it after that, then deploy it so its MLLP port starts listening.
Running it twice is safe: the second run overwrites the channel with the same file.

The engine's admin API is served over HTTPS with a self-signed certificate that the
container generates on first start. It is only reached inside the Compose network or
on localhost, so certificate checks are turned off for this one client.
"""

import base64
import os
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

CHANNEL_FILE = (
    Path(__file__).resolve().parents[3] / "interfaces" / "bridgelink" / "lab-results-in.xml"
)


def channel_file() -> Path:
    """The channel XML: LABPIPE_CHANNEL_FILE if set (the Docker image), else the repo copy."""
    return Path(os.environ.get("LABPIPE_CHANNEL_FILE", CHANNEL_FILE))


@dataclass(frozen=True)
class Engine:
    """Where the BridgeLink admin API is and how to log in."""

    url: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "Engine":
        return cls(
            url=os.environ.get("BRIDGELINK_URL", "https://localhost:8443").rstrip("/"),
            user=os.environ.get("BRIDGELINK_USER", "admin"),
            password=os.environ.get("BRIDGELINK_PASSWORD", "admin"),
        )

    @property
    def auth_header(self) -> str:
        """HTTP Basic authentication: "user:password", base64-encoded."""
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        return f"Basic {token}"


class EngineError(Exception):
    """The engine refused a request or never became ready."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def channel_id(xml: str) -> str:
    """The channel's id, read from the <id> element right under <channel>."""
    found = ET.fromstring(xml).findtext("id")
    if not found:
        raise EngineError("channel XML has no <id>")
    return found


def _request(engine: Engine, method: str, path: str, body: str | None = None) -> tuple[int, str]:
    headers = {
        "Authorization": engine.auth_header,
        # BridgeLink refuses API calls without this header (server.api.require-requested-with).
        "X-Requested-With": "labpipe",
        "Accept": "application/xml, text/plain",
    }
    data = None
    if body is not None:
        data = body.encode("utf-8")
        headers["Content-Type"] = "application/xml"
    req = urllib.request.Request(engine.url + path, data=data, method=method, headers=headers)
    # The engine is local, so skip any HTTP proxy, and accept its self-signed certificate.
    context = ssl._create_unverified_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read()[:300].decode("utf-8", "replace")
        raise EngineError(f"{method} {path} -> HTTP {err.code}: {detail}", err.code) from err


def wait_until_ready(engine: Engine, timeout: float = 300, interval: float = 3) -> str:
    """Poll the version endpoint until the engine answers. Return its version.

    Connection errors and 5xx answers mean "still starting", so keep polling. A 4xx
    (wrong password, for one) will not fix itself, so it stops at once.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _request(engine, "GET", "/api/server/version")[1]
        except (OSError, EngineError) as err:
            if isinstance(err, EngineError) and (err.status or 0) < 500:
                raise
            if time.monotonic() > deadline:
                raise EngineError(f"BridgeLink not ready at {engine.url}: {err}") from err
            time.sleep(interval)


def deploy_channel(engine: Engine, xml: str) -> str:
    """Create or overwrite the channel, deploy it, and return its state (STARTED when good)."""
    cid = channel_id(xml)
    status, existing = _request(engine, "GET", f"/api/channels/{cid}")
    if status == 200 and existing.strip():
        _request(engine, "PUT", f"/api/channels/{cid}?override=true", xml)
    else:
        _request(engine, "POST", "/api/channels", xml)
    _request(engine, "POST", f"/api/channels/{cid}/_deploy?returnErrors=true")
    return channel_state(engine, cid)


def channel_state(engine: Engine, cid: str, timeout: float = 60) -> str:
    """Wait for a deployed channel to leave its starting states and return the final one."""
    deadline = time.monotonic() + timeout
    while True:
        status, body = _request(engine, "GET", f"/api/channels/{cid}/status")
        state = ET.fromstring(body).findtext("state") if status == 200 and body.strip() else None
        if state in {"STARTED", "STOPPED", "PAUSED"} or time.monotonic() > deadline:
            return state or "UNDEPLOYED"
        time.sleep(1)
