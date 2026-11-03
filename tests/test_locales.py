import json
from pathlib import Path

import pytest


LOCALE_FILES = sorted((Path(__file__).parents[1] / "locales").glob("*/*.json"))


def _reject_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicate keys: {duplicates}"
    return dict(pairs)


@pytest.mark.parametrize(
    "path",
    LOCALE_FILES,
    ids=[f"{path.parent.name}/{path.name}" for path in LOCALE_FILES],
)
def test_locale_file_has_no_duplicate_keys(path):
    # json.loads keeps only the last of two equal keys, so a duplicate
    # silently replaces the first translation.
    json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
