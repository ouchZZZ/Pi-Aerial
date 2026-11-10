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


def test_web_updater_can_only_request_whitelisted_system_repair(tmp_path):
    import importlib.util
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location(
        "pi_aerial_update_helper_test", ROOT / "tools/apply_core_update.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    helper = tmp_path / "system_deps"
    helper.write_text("#!/bin/sh\nexit 0\n")

    with patch.object(module, "SYSTEM_DEPS_HELPER", helper):
        with patch.object(module.subprocess, "run") as run:
            module.ensure_system_dependencies()
        run.assert_called_once_with(
            ["sudo", "-n", str(helper), "--ensure"], check=True, timeout=420
        )

    with patch.object(module, "SYSTEM_DEPS_HELPER", tmp_path / "missing"):
        with patch.object(module.subprocess, "run") as run:
            module.ensure_system_dependencies()
        run.assert_not_called()


def test_public_installer_keeps_legacy_ssh_support():
    from pathlib import Path
    root = Path(__file__).parents[1]
    installer = (root / 'install.sh').read_text()
    github = (root / 'app/github_updates.py').read_text()
    assert 'https://github.com/ouchZZZ/Pi-Aerial.git' in installer
    assert 'git@github.com:ouchZZZ/Pi-Aerial.git' in installer
    assert 'test -f "$HOME/.ssh/pi_aerial_github" ||' not in installer
    assert 'PUBLIC_REMOTE' in github and 'LEGACY_SSH_REMOTE' in github
