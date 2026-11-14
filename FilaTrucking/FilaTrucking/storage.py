from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class LenientManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """collectstatic stores files as <name>.<content hash>.<ext> (cache busting) and {% static %} links to the hashed name.

    Lenient: when a file is not in the manifest and not even collected yet (the test runner always has DEBUG off and never
    runs collectstatic) the plain name is linked instead of the page failing with a 500."""

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            return name
