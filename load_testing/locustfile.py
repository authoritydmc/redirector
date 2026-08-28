from locust import HttpUser, TaskSet, task, between
import uuid

class RedirectUpstreamTasks(TaskSet):
    @task
    def create_random_redirect(self):
        shortcut = f"shortcut-{uuid.uuid4().hex[:8]}"
        target = "https://example.com"
        self.client.post(f"/edit/{shortcut}", data={"type": "redirect", "target": target}, name="/edit/<shortcut>")
        self.client.get(f"/{shortcut}", name="/<shortcut>")

    @task
    def create_and_test_google_redirect(self):
        shortcut = "google"
        target = "https://google.com"
        self.client.post(f"/edit/{shortcut}", data={"type": "redirect", "target": target}, name="/edit/google")
        self.client.get(f"/{shortcut}", name="/google")

    @task
    def check_upstreams_ui(self):
        self.client.get(f"/check-upstreams-ui/test-pattern", name="/check-upstreams-ui/<pattern>")

    @task
    def access_random_unknown_shortcut(self):
        unknown_shortcut = f"unknown-{uuid.uuid4().hex[:6]}"
        self.client.get(f"/{unknown_shortcut}", name="/<unknown-shortcut>")

    @task
    def create_dynamic_shortcut_and_redirect(self):
        shortcut = f"dyn-{uuid.uuid4().hex[:6]}/{{foo}}"
        target = "https://example.com/dyn/{foo}"
        self.client.post(f"/edit/", data={"pattern": shortcut, "type": "dynamic", "target": target}, name="/edit/dynamic")
        self.client.get(f"/dyn-{uuid.uuid4().hex[:6]}/bar", name="/dynamic-redirect")

    @task
    def create_user_dynamic_shortcut_and_redirect(self):
        shortcut = f"userdyn-{uuid.uuid4().hex[:6]}/[bar]"
        target = "https://example.com/userdyn/[bar]"
        self.client.post(f"/edit/", data={"pattern": shortcut, "type": "user-dynamic", "target": target}, name="/edit/user-dynamic")
        # Simulate user param prompt by providing value in URL
        self.client.get(f"/userdyn-{uuid.uuid4().hex[:6]}/baz", name="/user-dynamic-redirect")

    @task
    def upstream_cache_resync(self):
        self.client.get("/admin/upstream-cache/resync/Upstream1/test-pattern", name="/admin/upstream-cache/resync/<upstream>/<pattern>")

    @task
    def upstream_cache_purge(self):
        self.client.post("/admin/upstream-cache/purge/Upstream1", name="/admin/upstream-cache/purge/<upstream>")

    @task
    def upstream_logs(self):
        self.client.get("/admin/upstream-logs", name="/admin/upstream-logs")

class WebsiteUser(HttpUser):
    wait_time = between(1, 3)
    tasks = [RedirectUpstreamTasks]