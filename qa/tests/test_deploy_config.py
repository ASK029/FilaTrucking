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
