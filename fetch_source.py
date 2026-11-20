"""Fetch only the Python sources required to analyze diffs at run time."""
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REV = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
url = f"https://codeload.github.com/tobymao/sqlglot/zip/{REV}"
with tempfile.TemporaryFile() as archive:
    with urllib.request.urlopen(url, timeout=90) as response:
        shutil.copyfileobj(response, archive)
    with zipfile.ZipFile(archive) as files:
        for info in files.infolist():
            parts = Path(info.filename).parts
            if len(parts) < 2 or parts[0] != f"sqlglot-{REV}" or ".." in parts:
                continue
            if not info.is_dir() and (parts[1] in {"sqlglot", "tests", "sqlglotc"} or len(parts) == 2):
                path = Path("/src").joinpath(*parts[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(files.read(info))
