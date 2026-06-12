"""Tests for git hooks management."""

import os
import pytest


class TestHooksInstall:
    def test_install_creates_hooks(self, tmp_path):
        from tws_graph.hooks import install_hooks

        # Create a fake git repo
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir()

        installed, skipped = install_hooks(str(tmp_path))
        assert installed == 3
        assert skipped == 0

        for hook in ("post-commit", "post-merge", "post-checkout"):
            hook_path = hooks_dir / hook
            assert hook_path.is_file()
            content = hook_path.read_text()
            assert "tws-graph auto-sync" in content
            assert "tws_graph" in content

    def test_install_is_idempotent(self, tmp_path):
        from tws_graph.hooks import install_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        installed1, skipped1 = install_hooks(str(tmp_path))
        installed2, skipped2 = install_hooks(str(tmp_path))

        assert installed1 == 3
        assert skipped1 == 0
        assert installed2 == 0
        assert skipped2 == 3

    def test_install_no_git_dir(self, tmp_path):
        from tws_graph.hooks import install_hooks

        with pytest.raises(FileNotFoundError, match=".git"):
            install_hooks(str(tmp_path))

    def test_install_preserves_existing_content(self, tmp_path):
        from tws_graph.hooks import install_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir()

        # Pre-existing hook with custom content
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/sh\necho 'custom'\n")

        install_hooks(str(tmp_path))

        content = hook_path.read_text()
        assert "echo 'custom'" in content
        assert "tws-graph auto-sync" in content

    def test_install_uses_unix_line_endings(self, tmp_path):
        """Hook scripts must use LF, not CRLF. CRLF causes 'Exec format error' on Windows."""
        from tws_graph.hooks import install_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        install_hooks(str(tmp_path))

        hook_path = git_dir / "hooks" / "post-checkout"
        content = hook_path.read_bytes()
        assert b"\r" not in content, "Hook script contains CR (\\r) — use LF only"
        assert b"#!/usr/bin/env python\n" in content

    def test_install_worktree(self, tmp_path):
        from tws_graph.hooks import install_hooks

        # Simulate worktree: .git is a file pointing to main repo
        actual_git = tmp_path / "actual-git"
        actual_git.mkdir()
        (actual_git / "hooks").mkdir()

        git_file = tmp_path / ".git"
        git_file.write_text(f"gitdir: {actual_git}")

        installed, skipped = install_hooks(str(tmp_path))
        assert installed == 3

        for hook in ("post-commit", "post-merge", "post-checkout"):
            assert (actual_git / "hooks" / hook).is_file()


class TestHooksRemove:
    def test_remove_cleans_up(self, tmp_path):
        from tws_graph.hooks import install_hooks, remove_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        install_hooks(str(tmp_path))
        cleaned = remove_hooks(str(tmp_path))
        assert cleaned == 3

        # Hook files should be gone (they only had TWS content)
        for hook in ("post-commit", "post-merge", "post-checkout"):
            assert not (git_dir / "hooks" / hook).is_file()

    def test_remove_preserves_other_content(self, tmp_path):
        from tws_graph.hooks import install_hooks, remove_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        # Pre-existing hook
        hook_path = git_dir / "hooks" / "post-commit"
        hook_path.write_text("#!/bin/sh\necho 'custom hook'\n")

        install_hooks(str(tmp_path))
        remove_hooks(str(tmp_path))

        # Custom content should survive
        content = hook_path.read_text()
        assert "echo 'custom hook'" in content
        assert "tws-graph" not in content

    def test_remove_no_hooks(self, tmp_path):
        from tws_graph.hooks import remove_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        cleaned = remove_hooks(str(tmp_path))
        assert cleaned == 0

    def test_remove_no_git_dir(self, tmp_path):
        from tws_graph.hooks import remove_hooks

        with pytest.raises(FileNotFoundError, match=".git"):
            remove_hooks(str(tmp_path))


class TestHooksStatus:
    def test_status_all_installed(self, tmp_path):
        from tws_graph.hooks import install_hooks, status_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        install_hooks(str(tmp_path))
        st = status_hooks(str(tmp_path))

        assert st["post-commit"] is True
        assert st["post-merge"] is True
        assert st["post-checkout"] is True

    def test_status_none_installed(self, tmp_path):
        from tws_graph.hooks import status_hooks

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "hooks").mkdir()

        st = status_hooks(str(tmp_path))
        assert all(v is False for v in st.values())

    def test_status_no_git_dir(self, tmp_path):
        from tws_graph.hooks import status_hooks

        st = status_hooks(str(tmp_path))
        assert all(v is False for v in st.values())
