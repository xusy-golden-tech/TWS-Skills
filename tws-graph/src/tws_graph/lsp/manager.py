"""LSP server process manager.

Provides :class:`LspManager` which manages the lifecycle of multiple
language-specific LSP server processes.  Supports lazy startup, singleton
per language, crash restart with exponential backoff, and graceful
shutdown of all processes.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

from tws_graph.lsp.client import LspClient

if TYPE_CHECKING:
    from tws_graph.lsp.adapters.base import LspLanguageAdapter

logger = logging.getLogger(__name__)


class LspManager:
    """Manages lifecycle of multiple language-specific LSP server processes.

    Each language gets exactly one LspClient (singleton).  The client is
    started lazily on the first call to :meth:`get_client`.  If a client's
    health check fails, the manager attempts to restart the process with
    exponential backoff, up to a configurable maximum number of retries.

    Example usage::

        adapters = [PythonAdapter(), TypeScriptAdapter()]
        with LspManager("/path/to/project", adapters) as mgr:
            py_client = mgr.get_client("python")
            # ... use py_client ...
    """

    def __init__(
        self,
        workspace_root: str,
        adapters: list[LspLanguageAdapter],
        max_restart: int = 3,
        restart_base_delay: float = 5.0,
        heartbeat_timeout: float = 60.0,
    ) -> None:
        """Initialise the manager.

        Args:
            workspace_root: Absolute path to the project root served to LSP
                servers as ``rootUri``.
            adapters: List of :class:`LspLanguageAdapter` instances, one per
                supported language.
            max_restart: Maximum consecutive restart attempts before giving up
                (default 3).
            restart_base_delay: Base delay in seconds for the first restart;
                each subsequent retry doubles this value (default 5.0).
            heartbeat_timeout: Maximum elapsed seconds since last successful
                heartbeat before a client is considered unhealthy (default 60.0).
        """
        self.workspace_root = workspace_root
        self._adapters = adapters
        self.max_restart = max_restart
        self.restart_base_delay = restart_base_delay
        self.heartbeat_timeout = heartbeat_timeout

        # Internal state — no clients are started during __init__.
        self._clients: dict[str, LspClient] = {}
        self._restart_count: dict[str, int] = {}
        self._last_heartbeat_time: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_client(self, language: str) -> Optional[LspClient]:
        """Return the LspClient for *language*, starting it lazily if needed.

        When an existing client is unhealthy and the restart budget has not
        been exhausted, a new client process is started (with a backoff
        delay).  Returns ``None`` when no adapter is registered for the
        language or when all restart attempts have been consumed.
        """
        adapter = self._find_adapter(language)
        if adapter is None:
            return None

        existing = self._clients.get(language)

        if existing is not None:
            if self._is_healthy(existing, language):
                return existing

            # Existing client is unhealthy — check restart budget.
            restart_count = self._restart_count.get(language, 0)
            if restart_count >= self.max_restart:
                return None

            new_client = self._restart_client(language, adapter)
            if new_client is not None:
                return new_client
            # Restart failed to produce a viable client — fall back to the
            # existing client so that callers never see a spurious ``None``
            # when the restart budget has not been exhausted.
            return existing

        # No existing client — lazy start.
        client = self._start_client(language, adapter)
        if client is not None:
            self._clients[language] = client
            self._restart_count[language] = 0
            self._last_heartbeat_time[language] = time.monotonic()
        return client

    def is_available(self, language: str) -> bool:
        """Return ``True`` if an LSP client can be started for *language*.

        Checks whether an adapter is registered and whether a client can be
        started successfully.  Does not depend on a prior ``get_client`` call.
        """
        adapter = self._find_adapter(language)
        if adapter is None:
            return False

        existing = self._clients.get(language)
        if existing is not None:
            return True

        client = self._start_client(language, adapter)
        if client is not None:
            self._clients[language] = client
            self._restart_count[language] = 0
            self._last_heartbeat_time[language] = time.monotonic()
            return True
        return False

    def get_availability_report(self) -> dict[str, bool]:
        """Return a mapping of ``language`` → availability for all registered adapters."""
        return {
            adapter.language: self.is_available(adapter.language)
            for adapter in self._adapters
        }

    def health_status(self) -> dict[str, bool]:
        """Return a mapping of ``language`` → health for all registered adapters.

        A language is healthy when it has an active, responsive LspClient.
        Languages without a started client are reported as ``False``.
        """
        result: dict[str, bool] = {}
        for adapter in self._adapters:
            lang = adapter.language
            client = self._clients.get(lang)
            if client is None:
                result[lang] = False
            else:
                result[lang] = self._is_healthy(client, lang)
        return result

    def shutdown_all(self) -> None:
        """Gracefully shut down all active LSP server processes.

        Each client is sent ``shutdown`` → ``exit`` → ``close`` in sequence.
        Exceptions from any one client are isolated so that remaining
        clients still get shut down.
        """
        for lang, client in list(self._clients.items()):
            for action in ("shutdown", "exit", "close"):
                try:
                    getattr(client, action)()
                except Exception:
                    logger.debug(
                        "Error during %s of %s", action, lang, exc_info=True
                    )
        self._clients.clear()
        self._restart_count.clear()
        self._last_heartbeat_time.clear()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> LspManager:
        """Enter the runtime context — returns ``self``."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the runtime context — calls :meth:`shutdown_all`."""
        self.shutdown_all()
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_adapter(
        self, language: str
    ) -> Optional[LspLanguageAdapter]:
        """Return the first adapter whose ``language`` matches.

        Case-sensitive comparison.  Returns ``None`` when no adapter
        matches the given *language* name.
        """
        for adapter in self._adapters:
            if adapter.language == language:
                return adapter
        return None

    def _start_client(
        self,
        language: str,
        adapter: LspLanguageAdapter,
    ) -> Optional[LspClient]:
        """Create and return a new LspClient for *language*.

        Uses *adapter* to obtain the server command line and forwards the
        manager's :attr:`workspace_root`.  Returns ``None`` on failure
        (the exception is logged).
        """
        if adapter is None:
            return None
        try:
            command = adapter.get_server_command(self.workspace_root)
            client = LspClient(command, self.workspace_root)
            return client
        except Exception:
            logger.warning(
                "Failed to start LSP client for %s", language, exc_info=True
            )
            return None

    def _restart_client(
        self,
        language: str,
        adapter: LspLanguageAdapter,
    ) -> Optional[LspClient]:
        """Shut down the old client and start a new one with backoff delay.

        The old client is shut down gracefully (shutdown → exit → close).
        A backoff delay of ``base_delay * 2^restart_count`` is applied
        before starting the new process.  The restart counter is reset
        when the newly-created client is healthy.
        """
        old_client = self._clients.get(language)

        # Graceful shutdown of the old (crashed) client.
        if old_client is not None:
            for action in ("shutdown", "exit", "close"):
                try:
                    getattr(old_client, action)()
                except Exception:
                    pass

        # Exponential backoff: base_delay * 2^attempt.
        restart_count = self._restart_count.get(language, 0)
        delay = self.restart_base_delay * (2 ** restart_count)
        time.sleep(delay)

        # Start a fresh client.
        new_client = self._start_client(language, adapter)
        if new_client is not None:
            self._clients[language] = new_client
            self._last_heartbeat_time[language] = time.monotonic()
            # Reset counter on successful (healthy) restart; increment otherwise.
            if self._is_healthy(new_client, language):
                self._restart_count[language] = 0
            else:
                self._restart_count[language] = restart_count + 1
        else:
            # Start failed — increment the failure counter.
            self._restart_count[language] = restart_count + 1

        return new_client

    def _is_healthy(self, client: LspClient, language: str) -> bool:
        """Check whether *client* is still healthy.

        Returns ``False`` when:

        * The last successful heartbeat is older than
          :attr:`heartbeat_timeout` seconds.
        * The subprocess has exited (``_process.poll()`` returns non-None).
        * The client's ``_heartbeat()`` method returns ``False`` (e.g. the
          reader has reported EOF/error).

        A successful heartbeat updates the per-language timer so that
        consecutive calls within the timeout window don't re-check
        the heartbeat timer.
        """
        last = self._last_heartbeat_time.get(language)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed >= self.heartbeat_timeout:
                return False

        if client._process.poll() is not None:
            return False

        if not client._heartbeat():
            return False

        self._last_heartbeat_time[language] = time.monotonic()
        return True
