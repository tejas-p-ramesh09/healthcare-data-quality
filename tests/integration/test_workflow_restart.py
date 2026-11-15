"""Verify stage recovery with real HTTP disconnects and killed API processes."""

import http.client
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from healthcare_dq.db import connect, migrate
from tests.integration.test_workflow_api import HEADERS, REQUEST

SERVER = """
import os, sys, time
from pathlib import Path
import uvicorn
from healthcare_dq import ingestion
from healthcare_dq.api.app import create_app
from healthcare_dq.api.config import Settings
from healthcare_dq.workflow import service
from tests.integration.test_workflow_api import TOKEN, restricted
port, mode, marker, release, suffix = sys.argv[1:]
original_catalog = Settings.catalog
def catalog(self):
    data, original = original_catalog(self)
    def resolve(request):
        assets = original(request)
        for row in assets.spec["inputs"]:
            row["source"] += "-" + suffix
        return assets
    return data, resolve
Settings.catalog = catalog
def hold():
    Path(marker).touch()
    for _ in range(200):
        if Path(release).exists():
            return
        time.sleep(.05)
    raise TimeoutError
if mode in ("before", "disconnect"):
    original = ingestion.publish
    def publish(*args):
        original(*args)
        hold()
    ingestion.publish = publish
elif mode == "after":
    service.after_execute = hold
app = create_app(Settings(TOKEN, reader=lambda: restricted("workflow_reporter"),
                         executor=lambda: restricted("workflow_executor")))
uvicorn.run(app, host="127.0.0.1", port=int(port), access_log=False, log_level="critical")
"""


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class WorkflowRestart(unittest.TestCase):
    def test_kill_before_commit_kill_after_commit_and_http_disconnect(self):
        with connect("TEST_DATABASE_URL") as c:
            migrate(c)
        suffix = uuid4().hex
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=HEADERS, timeout=5) as client:
                def wait(predicate):
                    limit = time.monotonic() + 8
                    while time.monotonic() < limit:
                        if predicate():
                            return
                        time.sleep(.05)
                    self.fail("Timed out waiting for server checkpoint")

                def ready():
                    try:
                        return client.get("/health/live").status_code == 200
                    except httpx.TransportError:
                        return False

                def launch(mode):
                    marker, release = (root / (mode + name) for name in ("-marker", "-release"))
                    process = subprocess.Popen([sys.executable, "-c", SERVER, str(port), mode,
                                                str(marker), str(release), suffix])
                    self.addCleanup(stop, process)
                    wait(ready)
                    return process, marker, release

                def stop(process):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

                def send_and_disconnect(path, marker):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        connection.request("POST", path + "/advance", headers=HEADERS)
                        wait(marker.exists)
                    finally:
                        connection.close()

                def is_published():
                    with connect("TEST_DATABASE_URL") as c:
                        return c.execute("""SELECT count(*) FROM ingestion_batch b
                            JOIN ingestion_source s ON s.id=b.source_id
                            WHERE s.source_key=%s AND b.status='published'""",
                                         ("demo-source-a-" + suffix,)).fetchone()[0]

                process, marker, _ = launch("before")
                created = client.post("/v1/workflows", json=REQUEST, headers={"Idempotency-Key": suffix})
                self.assertEqual(created.status_code, 201, created.text)
                path = created.headers["location"]
                send_and_disconnect(path, marker)
                self.assertEqual(is_published(), 0)
                stop(process)

                process, marker, _ = launch("after")
                send_and_disconnect(path, marker)
                self.assertEqual(is_published(), 1)
                self.assertEqual(client.get(path).json()["stages"][0]["status"], "running")
                stop(process)

                process, marker, release = launch("disconnect")
                attached = client.post(path + "/advance").json()
                self.assertEqual(attached["stages"][0]["status"], "completed")
                self.assertEqual(attached["stages"][0]["attempt_count"], 2)
                self.assertEqual(is_published(), 1)
                send_and_disconnect(path, marker)
                release.touch()
                wait(lambda: client.get(path).json()["stages"][1]["status"] == "completed")
                stop(process)

                process, _, _ = launch("resume")
                for _ in range(3):
                    final = client.post(path + "/advance")
                    self.assertEqual(final.status_code, 200, final.text)
                self.assertEqual(final.json()["status"], "completed")
                self.assertEqual(client.get(path + "/recommendations?method=exact").json()["items"][0][
                    "disposition"], "suggested_match")
                stop(process)
