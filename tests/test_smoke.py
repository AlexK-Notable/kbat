"""Smoke test: kbat is importable and exposes a version string."""


def test_import_kbat() -> None:
    import kbat

    assert kbat.__version__
