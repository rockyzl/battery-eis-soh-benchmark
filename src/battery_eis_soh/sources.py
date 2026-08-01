"""Download, authenticate and safely extract the public Zenodo source archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path, PurePosixPath
from urllib.request import urlopen
from zipfile import ZipFile, ZipInfo


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(info: ZipInfo) -> PurePosixPath | None:
    member = PurePosixPath(info.filename.replace("\\", "/"))
    mode = (info.external_attr >> 16) & 0o170000
    if member.is_absolute() or ".." in member.parts or mode == stat.S_IFLNK:
        raise ValueError(f"unsafe ZIP member: {info.filename}")
    if not member.parts or member.parts[0] == "__MACOSX" or info.is_dir():
        return None
    return member


def download(url: str, destination: Path, expected_bytes: int, expected_md5: str) -> None:
    if destination.exists():
        if destination.stat().st_size == expected_bytes and md5(destination) == expected_md5:
            return
        raise ValueError(f"existing source file failed authentication: {destination}")
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        raise ValueError(f"remove or inspect stale partial download before retrying: {partial}")
    with urlopen(url) as response, partial.open("xb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    if partial.stat().st_size != expected_bytes or md5(partial) != expected_md5:
        raise ValueError(f"download failed source size/checksum verification: {destination.name}")
    partial.replace(destination)


def extract(archive: Path, root: Path) -> None:
    with ZipFile(archive) as handle:
        members = [(info, safe_member(info)) for info in handle.infolist()]
        for info, member in members:
            if member is None:
                continue
            target = root.joinpath(*member.parts)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    manifest = json.loads((project_root / "data/source_manifest.json").read_text())
    downloads = project_root / "data/raw/downloads"
    extracted = project_root / "data/raw/extracted"
    downloads.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)
    for source in manifest["files"]:
        archive = downloads / source["name"]
        download(source["download_url"], archive, source["bytes"], source["md5"])
        extract(archive, extracted)
        print(f"verified and extracted: {source['name']}")
