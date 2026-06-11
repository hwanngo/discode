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
