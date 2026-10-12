from __future__ import annotations

from discode.security.paths import canonicalize


class TestCanonicalize:
    def test_ssh_segment_denied(self) -> None:
        _, reason = canonicalize("/home/user/.ssh/config")
        assert reason is not None
        assert ".ssh" in reason

    def test_aws_segment_denied(self) -> None:
        _, reason = canonicalize("/home/user/.aws/credentials")
        assert reason is not None
        assert ".aws" in reason

    def test_safe_project_path_allowed(self) -> None:
        _, reason = canonicalize("/srv/projects/myapp")
        assert reason is None

    def test_proc_self_denied(self) -> None:
        _, reason = canonicalize("/proc/self")
        assert reason is not None
        assert "proc" in reason

    def test_sys_denied(self) -> None:
        _, reason = canonicalize("/sys/kernel")
        assert reason is not None

    def test_dev_denied(self) -> None:
        _, reason = canonicalize("/dev/null")
        assert reason is not None

    def test_gnupg_denied(self) -> None:
        _, reason = canonicalize("/root/.gnupg/pubring.kbx")
        assert reason is not None

    def test_realpath_returned(self) -> None:
        real, _ = canonicalize("/srv/projects/myapp")
        assert real.startswith("/")

    def test_tmp_path_allowed(self) -> None:
        # /tmp may resolve to /private/tmp on macOS, that's fine
        real, reason = canonicalize("/tmp/workspace")
        assert reason is None


class TestDenylistAnchoring:
    """Audit 2026-10-12: system dirs are denied at the root, not anywhere in the path."""

    def test_project_folders_named_like_system_dirs_allowed(self, tmp_path) -> None:
        for name in ("dev", "etc", "root", "sys", "boot", "proc"):
            project = tmp_path / name / "app"
            project.mkdir(parents=True)
            _, reason = canonicalize(str(project))
            assert reason is None, (name, reason)

    def test_system_roots_still_denied(self) -> None:
        for path in ("/etc/passwd", "/dev/null", "/proc/self", "/etc"):
            _, reason = canonicalize(path)
            assert reason is not None, path

    def test_dotfile_dirs_denied_anywhere(self, tmp_path) -> None:
        _, reason = canonicalize(str(tmp_path / "repo" / ".ssh"))
        assert reason is not None


def test_is_within_root_handles_trailing_slash_and_filesystem_root() -> None:
    from discode.security.paths import is_within_root

    assert is_within_root("/srv/app", "/srv/app")
    assert is_within_root("/srv/app/x", "/srv/app/")
    assert is_within_root("/srv/app/x", "/")
    assert not is_within_root("/srv/application", "/srv/app")
