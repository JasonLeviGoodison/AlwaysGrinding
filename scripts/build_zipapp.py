#!/usr/bin/env python3
from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lidguard"
DIST = ROOT / "dist"


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"Missing source package: {SRC}")

    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / "lid-guard.pyz"

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)
        shutil.copytree(SRC, staging / "lidguard")
        (staging / "__main__.py").write_text(
            "from lidguard.cli import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        zipapp.create_archive(
            staging,
            target=target,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )

    target.chmod(0o755)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

