"""Generate ``device-connect-driver/pyproject.toml`` with a pinned ``reachy-mini-driver`` direct URL.

``reachy-mini-app-assistant check`` installs ``device-connect-driver`` into a disposable venv; bare
``file:..`` relative URLs often resolve from the wrong working directory.

Usage:

  python device-connect-driver/sync_driver_dependency.py local
  reachy-mini-app-assistant check device-connect-driver

Publish to Hugging Face (after pushing this driver repo to GitHub):

  python device-connect-driver/sync_driver_dependency.py git --url 'git+https://github.com/you/reachy-mini-driver.git@main'
  reachy-mini-app-assistant publish device-connect-driver

Or install from PyPI (when published):

  python device-connect-driver/sync_driver_dependency.py pypi --version 0.2.0

Reset tracked placeholder for git (default before you fork):

  python device-connect-driver/sync_driver_dependency.py template
"""

from __future__ import annotations

import argparse
from pathlib import Path

PYPROJECT_BODY = """[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "reachy-mini-device-connect-space"
version = "0.1.0"
description = "Hugging Face Space package for the Reachy Mini Device Connect driver"
readme = "README.md"
requires-python = ">=3.11,<3.14"
keywords = ["reachy-mini", "reachy-mini-app", "device-connect"]
dependencies = [
  "reachy-mini>=1.7.3",
{DRIVER_DEPENDENCY_LINE}
]

[project.entry-points.reachy_mini_apps]
reachy-mini-device-connect-space = "reachy_mini_device_connect_space.main:ReachyMiniDeviceConnectSpace"

[tool.setuptools.packages.find]
where = ["."]
"""


def driver_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("local", help="Embed file:// dependency on parent monorepo checkout")

    gg = sub.add_parser("git", help="Embed git direct reference")
    gg.add_argument(
        "--url",
        required=True,
        help="e.g. git+https://github.com/you/reachy-mini-driver.git@main",
    )

    pg = sub.add_parser("pypi", help="Embed PyPI version")
    pg.add_argument("--version", required=True)

    tu = sub.add_parser(
        "template",
        help="Embed YOUR_GITHUB_USERNAME placeholder clone URL (offline-friendly default)",
    )
    tu.add_argument(
        "--username",
        default="YOUR_GITHUB_USERNAME",
        help="GitHub username or org owning reachy-mini-driver",
    )

    args = p.parse_args()
    out = Path(__file__).resolve().parent / "pyproject.toml"

    if args.cmd == "local":
        uri = driver_root().as_uri()
        dep = f'  "reachy-mini-driver[app,media] @ {uri}",'
    elif args.cmd == "git":
        dep = f'  "reachy-mini-driver[app,media] @ {args.url}",'
    elif args.cmd == "pypi":
        dep = f'  "reachy-mini-driver[app,media]=={args.version}",'
    else:
        dep = (
            f'  "reachy-mini-driver[app,media] @ git+https://github.com/'
            f'{args.username}/reachy-mini-driver.git@main",'
        )

    out.write_text(PYPROJECT_BODY.replace("{DRIVER_DEPENDENCY_LINE}", dep), encoding="utf-8")
    print(f"Wrote {out} ({args.cmd})")


if __name__ == "__main__":
    main()
