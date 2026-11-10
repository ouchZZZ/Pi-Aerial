"""Deployment scripts remain syntax-valid and use a fixed OS package whitelist."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_shell_syntax():
    for name in ("install.sh", "tools/system_deps.sh"):
        subprocess.run(["bash", "-n", str(ROOT / name)], check=True)


def test_system_packages_are_fixed_and_listable():
    result = subprocess.run(
        ["bash", str(ROOT / "tools/system_deps.sh"), "--list"],
        capture_output=True, text=True, check=True,
    )
    packages = result.stdout.splitlines()
    assert len(packages) == len(set(packages))
    for required in ("chromium", "python3-venv", "git", "openssh-client", "labwc"):
        assert required in packages
    assert not any(" " in p or "/" in p for p in packages)


def test_system_package_helper_does_not_accept_extra_arguments():
    result = subprocess.run(
        ["bash", str(ROOT / "tools/system_deps.sh"), "--list", "evil-package"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
