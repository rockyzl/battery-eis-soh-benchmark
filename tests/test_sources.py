from pathlib import Path
from zipfile import ZipInfo

import pytest

from battery_eis_soh.sources import safe_member


def test_safe_member_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError, match="unsafe ZIP member"):
        safe_member(ZipInfo("../escape.txt"))


def test_safe_member_ignores_macos_metadata() -> None:
    assert safe_member(ZipInfo("__MACOSX/._file")) is None


def test_safe_member_accepts_dataset_path() -> None:
    assert safe_member(ZipInfo("EIS data/example.txt")) == Path("EIS data/example.txt")
