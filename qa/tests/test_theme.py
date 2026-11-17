"""QA-053: light / dark theme wiring (the behaviour of the toggle script is tested in theme.test.cjs)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from .base import QABase

ROOT = Path(settings.BASE_DIR)
CSS = ROOT / "static" / "css" / "app.css"


class ThemeWiring(QABase):
    def test_every_page_loads_the_theme_script_before_painting_and_has_a_toggle(self):
        self.login()
        for url in ("/", "/shipments/", "/vehicles/", "/settings/system/"):
            with self.subTest(url=url):
                r = self.client.get(url)
                html = r.content.decode()
                self.assertIn("js/theme.js", html)
                self.assertLess(html.index("js/theme.js"), html.index("</head>"))  # in <head>: no flash of the wrong theme
                self.assertIn("data-theme-toggle", html)
                self.assertContains(r, 'aria-label="Switch to light theme"')

    def test_the_login_page_applies_the_saved_theme_too(self):
        html = self.client.get("/auth/login/").content.decode()
        self.assertIn("js/theme.js", html)

    def test_the_500_page_is_themed(self):
        self.assertIn("js/theme.js", (ROOT / "templates" / "500.html").read_text(encoding="utf-8"))

    def test_the_invoice_paper_keeps_its_own_palette(self):
        """The on-screen invoice is a white sheet in both themes: it must opt out of the light palette."""
        html = (ROOT / "shipments" / "templates" / "shipments" / "invoice_detail.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'data-palette="default"[^>]*class="bg-white')


class CompiledStylesheet(SimpleTestCase):
    """app.css is committed build output: fail loudly if it was not rebuilt after the theme config changed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = CSS.read_text(encoding="utf-8")

    def test_both_palettes_are_defined(self):
        self.assertIn('[data-theme=light]', self.css)
        self.assertIn('[data-palette=default]', self.css)
        self.assertEqual(len(re.findall(r"--c-slate-100:", self.css)), 2)  # dark/default and light

    def test_dark_values_are_the_original_tailwind_ones(self):
        self.assertIn("--c-slate-100:241 245 249", self.css)
        self.assertIn("--c-surface:15 23 42", self.css)
        self.assertIn("--c-accent:245 158 11", self.css)

    def test_light_values_swap_the_scale(self):
        self.assertIn("--c-slate-100:15 23 42", self.css)      # main text: dark slate
        self.assertIn("--c-surface-card:255 255 255", self.css)  # cards are white

    def test_utilities_read_the_variables(self):
        self.assertIn("rgb(var(--c-slate-100)", self.css)
        self.assertIn("rgb(var(--c-surface)", self.css)
        self.assertNotRegex(self.css, r"\.text-slate-100\{[^}]*color:#")  # not a baked-in hex colour
