"""Static checks of the deployment files (compose, nginx, env example). No Docker needed."""

import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


def compose(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


class SidecarIsNotExposed(SimpleTestCase):
    def test_prod_sidecar_has_no_published_port(self):
        """[QA-002] host port 3001 exposed the (formerly unauthenticated) session-control API to the internet."""
        svc = compose("docker-compose.prod.yml")["services"]["sidecar"]
        self.assertFalse(svc.get("ports"), "sidecar must not publish ports")

    def test_dev_sidecar_only_listens_on_loopback(self):
        for name, svc in compose("docker-compose.yml")["services"].items():
            if "sidecar" in name:
                for port in svc.get("ports", []):
                    self.assertTrue(str(port).startswith("127.0.0.1:"), f"{name}: {port}")

    def test_nginx_does_not_proxy_the_sidecar(self):
        conf = (ROOT / "nginx.conf").read_text(encoding="utf-8")
        self.assertNotIn("/sidecar/", conf)
        self.assertNotIn("sidecar:3001", conf)


class NoSecretsInTrackedConfig(SimpleTestCase):
    def test_no_hardcoded_whatsapp_secret(self):
        for path in ("docker-compose.yml", "docker-compose.prod.yml", ".env.example",
                     "sidecar/index.js", "FilaTrucking/shipments/api.py", "FilaTrucking/FilaTrucking/settings.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            if path.endswith("settings.py"):
                # the only mention allowed is the deny-list of compromised values
                text = re.sub(r"_COMPROMISED_WHATSAPP_SECRETS = \{[^}]*\}", "", text)
            if path == "sidecar/index.js":
                text = re.sub(r"COMPROMISED_SECRETS = new Set\(\[[^\]]*\]\)", "", text)
            self.assertNotIn("fila_secret_2026", text, path)


import os
import subprocess
import sys

from django.conf import settings as dj_settings


def _import_settings(**env):
    """Import FilaTrucking.settings in a clean subprocess with exactly the given environment."""
    code = ("import os,django;os.environ['DJANGO_SETTINGS_MODULE']='FilaTrucking.settings';django.setup();"
            "from django.conf import settings as s;"
            "print('RESULT', s.DEBUG, ','.join(s.ALLOWED_HOSTS), ','.join(s.CSRF_TRUSTED_ORIGINS))")
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH", "HOME", "USERPROFILE")}
    keep["DOTENV_PATH"] = str(ROOT / "qa" / ".no-such-env")
    keep.update(env)
    return subprocess.run([sys.executable, "-c", code], env=keep, cwd=dj_settings.BASE_DIR, capture_output=True, text=True)


GOOD = dict(SECRET_KEY="k" * 60, DATABASE_URL="sqlite:///:memory:", WHATSAPP_INGEST_SECRET="s" * 40,
            CELERY_BROKER_URL="memory://")


class SettingsFailFast(SimpleTestCase):
    """[QA-006] the repository is public: no secret or credential may have a default value."""

    def test_complete_environment_starts(self):
        r = _import_settings(**GOOD)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RESULT False localhost,127.0.0.1 ", r.stdout)  # debug off, no ngrok wildcard, no trusted origins

    def test_missing_secret_key_or_database_url_stops_startup(self):
        for missing in ("SECRET_KEY", "DATABASE_URL"):
            with self.subTest(missing=missing):
                env = {k: v for k, v in GOOD.items() if k != missing}
                r = _import_settings(**env)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(missing, r.stderr)

    def test_debug_never_rescues_a_missing_database_url(self):
        env = {k: v for k, v in GOOD.items() if k != "DATABASE_URL"}
        r = _import_settings(DEBUG="True", **env)
        self.assertNotEqual(r.returncode, 0)

    def test_dev_run_without_secret_key_uses_a_throwaway_key(self):
        env = {k: v for k, v in GOOD.items() if k != "SECRET_KEY"}
        self.assertEqual(_import_settings(DEBUG="True", **env).returncode, 0)


class NoCredentialsInTrackedFiles(SimpleTestCase):
    CRED_URL = re.compile(r"(?:mysql|postgres(?:ql)?)://([^:@\s/]+):([^@\s]+)@")

    def tracked_text_files(self):
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
        for name in out:
            if name.startswith(("qa/evidence/", "qa/screenshots/", "docs/")) or name.endswith(
                    (".png", ".jpg", ".jpeg", ".pdf", ".xlsx", ".zip", ".woff2", ".ico")):
                continue
            path = ROOT / name
            if path.is_file():
                try:
                    yield name, path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

    def test_no_database_url_with_a_real_password_or_root_user(self):
        placeholders = ("CHANGE_ME", "${", "<", "password", "test", "pass")
        for name, text in self.tracked_text_files():
            for user, password in self.CRED_URL.findall(text):
                with self.subTest(file=name):
                    self.assertNotEqual(user, "root", f"{name}: root database user in a URL")
                    self.assertTrue(any(p in password for p in placeholders), f"{name}: literal DB password in a URL")

    def test_no_insecure_django_secret_key_literal(self):
        for name, text in self.tracked_text_files():
            if name.startswith("qa/"):
                continue
            for m in re.finditer(r"django-insecure-[^\s'\"`]+", text):
                self.assertTrue(m.group(0).startswith("django-insecure-local-development-only"), f"{name}: {m.group(0)[:20]}…")


class PythonDependencies(SimpleTestCase):
    """[QA-021] requirements.txt is the single, fully pinned dependency list used by the Docker build."""

    def lines(self):
        raw = (ROOT / "requirements.txt").read_bytes()
        self.assertFalse(raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")), "must be plain UTF-8 without BOM")
        return [ln.strip() for ln in raw.decode("utf-8").splitlines() if ln.strip() and not ln.startswith("#")]

    def test_every_requirement_is_pinned(self):
        for ln in self.lines():
            self.assertRegex(ln, r"^[A-Za-z0-9_.\-]+==[A-Za-z0-9_.\-]+$", ln)

    def test_installed_django_matches_the_pin_and_is_a_patched_release(self):
        import django
        pins = dict(ln.split("==") for ln in self.lines())
        self.assertEqual(django.get_version(), pins["Django"])
        major_minor_patch = tuple(int(x) for x in pins["Django"].split(".")[:3])
        self.assertGreaterEqual(major_minor_patch, (6, 0, 8), "Django 6.0.1 has 40+ published security advisories")

    def test_unused_and_duplicate_requirement_files_are_gone(self):
        for name in ("requirements_clean.txt", "requirements_utf8.txt"):
            self.assertFalse((ROOT / name).exists(), f"{name} was a stale copy of requirements.txt")

    def test_dockerfile_installs_only_from_the_pinned_file(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(text, r"pip install --no-cache-dir -r requirements.txt\s*\n")


class NoBusinessDataInGit(SimpleTestCase):
    """[QA-024] the repository is public: uploads, statements and exports must not be tracked or baked into images."""

    def tracked(self):
        return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()

    def test_no_uploads_exports_or_spreadsheets_are_tracked(self):
        bad = [f for f in self.tracked() if f.startswith("FilaTrucking/media/") or f == "gomotive_resp.json"
               or (f.startswith("docs/") and f.lower().endswith((".csv", ".xlsx", ".xls")))]
        self.assertEqual(bad, [])

    def test_gitignore_blocks_them_again(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for rule in ("FilaTrucking/media/", "docs/*.csv", "docs/*.xlsx", "gomotive_resp.json"):
            self.assertIn(rule, text)

    def test_docker_build_context_excludes_media_and_secrets(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for rule in ("FilaTrucking/media/", ".env"):
            self.assertIn(rule, text)


class ProductionHardeningQA025(SimpleTestCase):
    def services(self):
        return compose("docker-compose.prod.yml")["services"]

    def test_only_nginx_publishes_ports(self):
        """[QA-025] Django used to be reachable on host port 8888 over plain HTTP, bypassing nginx and TLS."""
        published = {name: svc["ports"] for name, svc in self.services().items() if svc.get("ports")}
        self.assertEqual(set(published), {"nginx"}, published)
        self.assertEqual(sorted(published["nginx"]), ["443:443", "80:80"])

    def test_django_containers_do_not_run_as_root(self):
        for name in ("django", "celery-worker", "celery-beat"):
            with self.subTest(service=name):
                user = self.services()[name]["user"]
                self.assertIn("APP_UID", user)
                self.assertIn("APP_GID", user)

    def test_sidecar_image_drops_root_before_running_the_bot(self):
        text = (ROOT / "sidecar" / "docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("setpriv --reuid=node", text)
        self.assertIn("chown -R node:node /app/sidecar/auth_data", text)  # old root-owned volumes keep working
        dockerfile = (ROOT / "sidecar" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENTRYPOINT", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^USER root")

    def test_every_long_running_service_has_a_healthcheck(self):
        services = self.services()
        for name in ("mysql", "redis", "django", "celery-worker", "nginx"):
            self.assertIn("healthcheck", services[name], name)

    def test_logs_are_rotated_and_memory_is_capped(self):
        for name, svc in self.services().items():
            with self.subTest(service=name):
                self.assertEqual(svc["logging"]["options"]["max-size"], "10m")
                if name != "certbot":
                    self.assertIn("mem_limit", svc)


class CertificateRenewalQA026(SimpleTestCase):
    def test_nginx_reloads_periodically_so_renewed_certificates_are_picked_up(self):
        """[QA-026] certbot renews into a shared volume, but nginx only read the certificate at start-up."""
        command = " ".join(compose("docker-compose.prod.yml")["services"]["nginx"]["command"])
        self.assertIn("nginx -s reload", command)
        match = re.search(r"sleep (\d+)h", command)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 12, "reload at least twice a day (certbot renews 30 days before expiry)")
        self.assertIn("exec nginx -g 'daemon off;'", command)  # nginx stays PID 1 so signals/restarts work

    def test_certbot_still_renews_regularly(self):
        text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        self.assertIn("certbot renew", text)

    def test_nginx_config_has_no_deprecated_http2_listen_flag(self):
        conf = (ROOT / "nginx.conf").read_text(encoding="utf-8")
        self.assertNotIn("ssl http2", conf)
        self.assertIn("http2 on;", conf)


class BackupsQA027(SimpleTestCase):
    def test_prod_compose_has_a_backup_service_writing_to_the_host(self):
        """[QA-027] production data lived in exactly one docker volume with no backup of any kind."""
        svc = compose("docker-compose.prod.yml")["services"]["backup"]
        self.assertIn("./backups:/backups", svc["volumes"])
        self.assertIn("/deploy/backup_mysql.sh", " ".join(svc["entrypoint"]))
        self.assertEqual(svc["image"], compose("docker-compose.prod.yml")["services"]["mysql"]["image"])  # same mysqldump version
        self.assertEqual(svc["depends_on"]["mysql"]["condition"], "service_healthy")

    def test_backup_script_verifies_and_rotates(self):
        text = (ROOT / "deploy" / "backup_mysql.sh").read_text(encoding="utf-8")
        for needle in ("--single-transaction", "gzip -t", "Dump completed", "-mtime +", ".partial"):
            self.assertIn(needle, text)
        self.assertNotRegex(text, r"-p[A-Za-z0-9]", "the password must go through MYSQL_PWD, not the command line")

    def test_restore_script_defaults_to_a_scratch_database(self):
        text = (ROOT / "deploy" / "restore_mysql.sh").read_text(encoding="utf-8")
        self.assertIn("filatrucking_restore_test", text)

    def test_backups_are_not_committed(self):
        self.assertIn("backups/", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "deploy" / "README.md").exists())
