"""Static file storage for this project."""

from django.core.files.storage import FileSystemStorage
from whitenoise.storage import CompressedManifestStaticFilesStorage


class LenientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Content-hashed static files that fall back gracefully when unresolved.

    Static files are served with a long cache lifetime, so filenames are
    content-hashed: whenever a file changes, its URL changes, and a cached copy is
    never requested again. That is what stops a stale stylesheet being served
    alongside newer markup — a mismatch that once left the navigation unstyled and
    images unsized for visitors whose browsers held an older copy.

    The default behaviour raises ``ValueError`` whenever the manifest cannot
    resolve a name. That is correct in production, where ``collectstatic`` runs as
    an ExecStartPre and the manifest is always present, but it breaks every
    template render elsewhere: the Django test runner forces ``DEBUG=False``, so
    the whole suite would fail on a fresh checkout that has never collected
    static files.

    ``manifest_strict = False`` alone is not enough — it falls through to hashing
    the source file, which also needs the file to be collected. So ``url()``
    falls back to the plain ``/static/<name>`` path instead. Hashing is unaffected
    in production; a genuinely missing reference is caught by
    ``StaticAssetTests``, which is both a clearer failure and earlier than a
    crash mid-render.
    """

    manifest_strict = False

    def url(self, name, force=False):
        try:
            return super().url(name, force=force)
        except ValueError:
            # No manifest, or the file is not collected (tests, fresh checkout).
            return FileSystemStorage.url(self, name)
