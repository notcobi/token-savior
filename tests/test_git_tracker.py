"""Unit tests for git_tracker module."""

import os
import tempfile
from unittest.mock import patch, MagicMock
import subprocess

from token_savior.git_tracker import (
    is_git_repo,
    get_head_commit,
    get_changed_files,
    get_git_status,
    GitChangeSet,
)


class TestIsGitRepo:
    def test_returns_true_for_git_repo(self):
        # is_git_repo uses filesystem check (.git directory), not subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".git"))
            assert is_git_repo(tmpdir) is True

    def test_returns_false_for_non_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_git_repo(tmpdir) is False

    def test_returns_false_when_git_not_installed(self):
        # Filesystem check never calls subprocess, so always returns False for non-git dirs
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_git_repo(tmpdir) is False

    def test_returns_false_on_timeout(self):
        # Filesystem check never times out; non-git dir returns False
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_git_repo(tmpdir) is False

    def test_finds_git_in_parent(self):
        # Should walk up to parent containing .git
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".git"))
            subdir = os.path.join(tmpdir, "src", "pkg")
            os.makedirs(subdir)
            assert is_git_repo(subdir) is True


class TestGetHeadCommit:
    def test_returns_commit_hash(self):
        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n")
            assert get_head_commit("/some/path") == "abc123"

    def test_returns_none_on_failure(self):
        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert get_head_commit("/some/path") is None

    def test_returns_none_when_git_not_installed(self):
        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert get_head_commit("/some/path") is None


class TestGetChangedFiles:
    def test_returns_empty_when_since_ref_is_none(self):
        changeset = get_changed_files("/some/path", None)
        assert changeset.is_empty

    def _strip_config_flags(self, cmd):
        """Remove -c <value> pairs from a git command list."""
        result = []
        skip_next = False
        for arg in cmd:
            if skip_next:
                skip_next = False
                continue
            if arg == "-c":
                skip_next = True
                continue
            result.append(arg)
        return result

    def _is_committed_diff(self, cmd):
        """git diff --name-status <ref> HEAD  (with _GIT_NO_CRED flags interspersed)."""
        return self._strip_config_flags(cmd) == ["git", "diff", "--name-status", "abc123", "HEAD"]

    def _is_unstaged_diff(self, cmd):
        return self._strip_config_flags(cmd) == ["git", "diff", "--name-status"]

    def _is_ls_files(self, cmd):
        return "ls-files" in cmd

    def test_parses_modified_files(self):
        def mock_run(cmd, **kwargs):
            if self._is_committed_diff(cmd):
                return MagicMock(returncode=0, stdout="M\tfoo.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "foo.py" in changeset.modified

    def test_parses_added_files(self):
        def mock_run(cmd, **kwargs):
            if self._is_committed_diff(cmd):
                return MagicMock(returncode=0, stdout="A\tnew_file.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "new_file.py" in changeset.added

    def test_parses_deleted_files(self):
        def mock_run(cmd, **kwargs):
            if self._is_committed_diff(cmd):
                return MagicMock(returncode=0, stdout="D\told_file.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "old_file.py" in changeset.deleted

    def test_rename_handling(self):
        def mock_run(cmd, **kwargs):
            if self._is_committed_diff(cmd):
                return MagicMock(returncode=0, stdout="R100\told.py\tnew.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "old.py" in changeset.deleted
            assert "new.py" in changeset.added

    def test_overlap_resolution_added_and_deleted_becomes_modified(self):
        """If a file appears in both added and deleted, treat as modified."""

        def mock_run(cmd, **kwargs):
            if self._is_committed_diff(cmd):
                return MagicMock(returncode=0, stdout="D\toverlap.py\n")
            if self._is_unstaged_diff(cmd):
                return MagicMock(returncode=0, stdout="A\toverlap.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "overlap.py" in changeset.modified
            assert "overlap.py" not in changeset.added
            assert "overlap.py" not in changeset.deleted

    def test_untracked_files_added(self):
        def mock_run(cmd, **kwargs):
            if self._is_ls_files(cmd):
                return MagicMock(returncode=0, stdout="untracked.py\n")
            return MagicMock(returncode=0, stdout="")

        with patch("token_savior.git_tracker.subprocess.run", side_effect=mock_run):
            changeset = get_changed_files("/some/path", "abc123")
            assert "untracked.py" in changeset.added

    def test_graceful_failure_git_not_found(self):
        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            changeset = get_changed_files("/some/path", "abc123")
            assert changeset.is_empty


class TestGitChangeSet:
    def test_is_empty_true(self):
        cs = GitChangeSet()
        assert cs.is_empty

    def test_is_empty_false_with_modified(self):
        cs = GitChangeSet(modified=["foo.py"])
        assert not cs.is_empty

    def test_is_empty_false_with_added(self):
        cs = GitChangeSet(added=["foo.py"])
        assert not cs.is_empty

    def test_is_empty_false_with_deleted(self):
        cs = GitChangeSet(deleted=["foo.py"])
        assert not cs.is_empty


class TestGetGitStatus:
    def test_parses_branch_tracking_and_file_buckets(self):
        output = (
            "## main...origin/main [ahead 2, behind 1]\n"
            "M  staged.py\n"
            " M unstaged.py\n"
            "MM both.py\n"
            "?? new.py\n"
            "UU conflict.py\n"
        )

        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            result = get_git_status("/some/path")

        assert result["ok"] is True
        assert result["branch"] == "main"
        assert result["upstream"] == "origin/main"
        assert result["ahead"] == 2
        assert result["behind"] == 1
        assert result["staged"] == ["staged.py", "both.py"]
        assert result["unstaged"] == ["unstaged.py", "both.py"]
        assert result["untracked"] == ["new.py"]
        assert result["conflicted"] == ["conflict.py"]
        assert result["clean"] is False

    def test_returns_error_when_git_fails(self):
        with patch("token_savior.git_tracker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repo")
            result = get_git_status("/some/path")

        assert result == {"ok": False, "error": "fatal: not a git repo"}
