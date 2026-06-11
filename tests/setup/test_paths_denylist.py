from __future__ import annotations

import os

import pytest

from discode.security.paths import canonicalize


@pytest.mark.parametrize(
    "rel",
    [
        ".docker/config.json",
        ".kube/config",
        ".netrc",
        ".git-credentials",
        ".local/share/secret",
        ".aws/credentials",
        ".ssh/id_rsa",
        ".gnupg/secring.gpg",
        ".npmrc",
        ".gcloud/creds",
        ".azure/token",
        ".config/secret",
    ],
)
def test_high_value_secret_paths_rejected(rel: str) -> None:
    home = os.path.expanduser("~")
    path = os.path.join(home, rel)
    _real, reason = canonicalize(path)
    assert reason is not None, f"expected {path} to be denied"


def test_normal_project_dir_allowed(tmp_path) -> None:
    proj = tmp_path / "myproject" / "src"
    proj.mkdir(parents=True)
    _real, reason = canonicalize(str(proj))
    assert reason is None, f"expected normal dir to be allowed, got {reason}"


def test_env_credential_file_rejected(tmp_path) -> None:
    env_file = tmp_path / "myproject" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("SECRET=1")
    _real, reason = canonicalize(str(env_file))
    assert reason is not None, "expected .env file to be denied"


def test_symlink_into_ssh_rejected(tmp_path) -> None:
    home = os.path.expanduser("~")
    ssh_dir = os.path.join(home, ".ssh")
    link = tmp_path / "innocent"
    os.symlink(ssh_dir, link)
    _real, reason = canonicalize(str(link / "id_rsa"))
    assert reason is not None, "expected symlink into ~/.ssh to be denied"
