"""Locust load script for the Redirector v3 FastAPI backend (EPIC-01/06).

Covers: shortcuts CRUD + bulk-delete, the resolve hot path (static, dynamic,
user-dynamic, unknown), upstreams CRUD + cache purge + SSE check stream,
metrics, QR, and ops probes. Mutations are admin-gated (RBAC), so each VU
logs in on start and reuses its JWT; pure reads stay anonymous-safe.

Password: REDIRECTOR_ADMIN_PASSWORD (must match the target server; the
dev/smoke default is "admin"). Run: locust -f load_testing/locustfile_v3.py
--host=http://127.0.0.1:8123
"""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, TaskSet, between, task


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class V3ApiTasks(TaskSet):
    def _resolve(self, subpath: str, name: str) -> None:
        # Never follow redirects: targets are external, and following them
        # would attribute the target's status to this endpoint. Accept the
        # 302 (delay=0) or the 200 countdown page (delay>0).
        with self.client.get(
            f"/{subpath}", name=name, allow_redirects=False, catch_response=True
        ) as resp:
            if resp.status_code not in (200, 302):
                resp.failure(f"resolve: {resp.status_code}")
            elif resp.status_code == 302 and not resp.headers.get("Location"):
                resp.failure("302 without Location")

    @task(5)
    def create_and_resolve_static(self) -> None:
        pattern = _uid("load")
        with self.client.post(
            "/api/v1/shortcuts",
            json={"pattern": pattern, "target": "https://example.com/x"},
            name="/api/v1/shortcuts [POST]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 201:
                resp.failure(f"create static: {resp.status_code}")
                return
        self._resolve(pattern, "/<shortcut>")

    @task(3)
    def resolve_unknown(self) -> None:
        # 404 is the expected outcome here, not a failure.
        with self.client.get(
            f"/unknown-{uuid.uuid4().hex[:6]}",
            name="/<unknown-shortcut>",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            else:
                resp.failure(f"unknown should 404, got {resp.status_code}")

    @task(2)
    def create_and_resolve_dynamic(self) -> None:
        pattern = _uid("loaddyn")
        with self.client.post(
            "/api/v1/shortcuts",
            json={
                "pattern": pattern,
                "type": "dynamic",
                "target": "https://example.com/d/[arg]",
            },
            name="/api/v1/shortcuts [POST]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 201:
                resp.failure(f"create dynamic: {resp.status_code}")
                return
        self._resolve(f"{pattern}/bar", "/<dynamic-shortcut>")

    @task(2)
    def create_and_resolve_user_dynamic(self) -> None:
        pattern = _uid("loaduserdyn")
        with self.client.post(
            "/api/v1/shortcuts",
            json={
                "pattern": pattern,
                "type": "user-dynamic",
                "target": "https://example.com/u/[arg]",
            },
            name="/api/v1/shortcuts [POST]",
            catch_response=True,
        ) as resp:
            if resp.status_code != 201:
                resp.failure(f"create user-dynamic: {resp.status_code}")
                return
        self._resolve(f"{pattern}/baz", "/<user-dynamic-shortcut>")

    @task(2)
    def list_shortcuts(self) -> None:
        self.client.get("/api/v1/shortcuts?page=1&pageSize=20", name="/api/v1/shortcuts [GET]")

    @task(1)
    def bulk_delete_roundtrip(self) -> None:
        pattern = _uid("loadbulk")
        self.client.post(
            "/api/v1/shortcuts",
            json={"pattern": pattern, "target": "https://example.com/b"},
            name="/api/v1/shortcuts [POST]",
        )
        self.client.post(
            "/api/v1/shortcuts/bulk-delete",
            json={"patterns": [pattern]},
            name="/api/v1/shortcuts/bulk-delete",
        )

    @task(2)
    def upstream_crud_and_cache(self) -> None:
        name = _uid("loadup")
        created = self.client.post(
            "/api/v1/upstreams",
            json={"name": name, "base_url": "https://example.com"},
            name="/api/v1/upstreams [POST]",
        )
        self.client.get("/api/v1/upstreams", name="/api/v1/upstreams [GET]")
        self.client.get("/api/v1/upstreams/cache", name="/api/v1/upstreams/cache [GET]")
        if created.status_code == 201:
            uid = created.json()["id"]
            self.client.delete(f"/api/v1/upstreams/{uid}", name="/api/v1/upstreams/<id> [DELETE]")
        self.client.delete(
            "/api/v1/upstreams/cache",
            name="/api/v1/upstreams/cache [DELETE]",
        )

    @task(1)
    def upstream_check_stream(self) -> None:
        name = _uid("loadstream")
        created = self.client.post(
            "/api/v1/upstreams",
            json={"name": name, "base_url": "https://example.com"},
            name="/api/v1/upstreams [POST]",
        )
        # Finite SSE: the service closes the stream after the terminal event.
        # NOTE: each check performs a real outbound HTTP call, so stream
        # throughput is bound by upstream latency — seed few upstreams.
        self.client.get(
            f"/api/v1/upstreams/check/stream/{name}-probe",
            name="/api/v1/upstreams/check/stream [GET]",
            timeout=30,
        )
        if created.status_code == 201:
            uid = created.json()["id"]
            self.client.delete(f"/api/v1/upstreams/{uid}", name="/api/v1/upstreams/<id> [DELETE]")

    @task(2)
    def metrics_and_qr(self) -> None:
        self.client.get("/api/v1/metrics/kpi", name="/api/v1/metrics/kpi")
        self.client.get("/api/v1/metrics/live", name="/api/v1/metrics/live")
        pattern = _uid("loadqr")
        self.client.post(
            "/api/v1/shortcuts",
            json={"pattern": pattern, "target": "https://example.com/q"},
            name="/api/v1/shortcuts [POST]",
        )
        self.client.get(f"/api/v1/qr?pattern={pattern}", name="/api/v1/qr [GET]")
        self.client.get(f"/qr/{pattern}", name="/qr/<pattern>")

    @task(1)
    def cache_resync_and_entry_purge(self) -> None:
        name = _uid("loadresync")
        created = self.client.post(
            "/api/v1/upstreams",
            json={"name": name, "base_url": "https://example.com"},
            name="/api/v1/upstreams [POST]",
        )
        if created.status_code != 201:
            return
        uid = created.json()["id"]
        # Single outbound check per resync; entry purge exercises the row path.
        self.client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": name, "pattern": "home"},
            name="/api/v1/upstreams/cache/resync",
        )
        self.client.delete(
            f"/api/v1/upstreams/cache/{name}/home",
            name="/api/v1/upstreams/cache/<upstream>/<pattern> [DELETE]",
        )
        self.client.get("/api/v1/upstreams/check-logs", name="/api/v1/upstreams/check-logs")
        self.client.delete(f"/api/v1/upstreams/{uid}", name="/api/v1/upstreams/<id> [DELETE]")

    @task(1)
    def ops_probes(self) -> None:
        self.client.get("/healthz")
        self.client.get("/health")
        self.client.get("/readyz")


class V3ApiUser(HttpUser):
    wait_time = between(0.5, 2.0)
    tasks = [V3ApiTasks]

    def on_start(self) -> None:
        """Authenticate once per VU: mutations require an admin JWT (RBAC)."""
        password = os.environ.get("REDIRECTOR_ADMIN_PASSWORD", "admin")
        resp = self.client.post("/api/v1/auth/login", json={"password": password})
        if resp.status_code != 200:
            raise RuntimeError(f"load-user login failed: {resp.status_code}")
        self.client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
