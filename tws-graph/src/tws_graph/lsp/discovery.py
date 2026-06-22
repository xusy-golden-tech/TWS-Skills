"""Language server discovery with three availability levels.

L1: ``shutil.which`` lookup on ``PATH`` using the binary name from the adapter.
L2: ``TWS_LSP_{LANG}_BINARY`` environment variable overrides the PATH result.
L3: ``adapter.check_availability()`` gates the final answer — if it returns
    ``False`` the server is marked unavailable regardless of L1/L2.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

from tws_graph.lsp.adapters.base import LspLanguageAdapter


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of LSP server discovery for a single language.

    Attributes:
        language: Language name from the adapter (e.g. ``"python"``,
            ``"typescript"``).
        binary: Binary name extracted from ``adapter.get_server_command()[0]``.
        available: Whether the LSP server is usable.
        path: Absolute filesystem path to the server binary (only set when
            *available* is ``True``).
        error: Human-readable description of why the server is unavailable
            (only set when *available* is ``False``).
    """

    language: str
    binary: str
    available: bool
    path: Optional[str] = None
    error: Optional[str] = None


def discover(adapter: LspLanguageAdapter) -> DiscoveryResult:
    """Discover whether the LSP server for *adapter* is available.

    Three-level availability check:

    **L1 — PATH lookup**
        ``shutil.which(adapter.get_server_command()[0])`` searches for the
        binary on the system ``PATH``.

    **L2 — environment variable override**
        If ``TWS_LSP_{LANG}_BINARY`` is set (e.g.
        ``TWS_LSP_PYTHON_BINARY=/custom/path``), its value replaces the
        PATH lookup result as the server path.

    **L3 — adapter availability gate**
        ``adapter.check_availability()`` is the final gate.  Even when L1
        or L2 finds a binary, if this method returns ``False`` the server
        is marked unavailable.

    Args:
        adapter: A concrete :class:`LspLanguageAdapter` instance.

    Returns:
        A :class:`DiscoveryResult` describing the discovery outcome.
    """
    language = adapter.language
    binary = adapter.get_server_command("")[0]

    # -- L2: environment variable override --
    env_key = f"TWS_LSP_{language.upper()}_BINARY"
    env_path = os.environ.get(env_key)

    if env_path:
        # L2 override: bypass adapter.check_availability() (which does
        # a PATH-based lookup that won't find a custom path) and instead
        # directly verify that the file exists and is executable.
        if os.path.isfile(env_path) and os.access(env_path, os.X_OK):
            return DiscoveryResult(
                language=language,
                binary=binary,
                available=True,
                path=env_path,
            )
        else:
            return DiscoveryResult(
                language=language,
                binary=binary,
                available=False,
                error=(
                    f"LSP binary override '{env_path}' (from {env_key}) "
                    "is not an executable file"
                ),
            )

    # -- L1: PATH lookup --
    try:
        found_path = shutil.which(binary)
    except OSError as e:
        return DiscoveryResult(
            language=language,
            binary=binary,
            available=False,
            error=f"OSError during PATH lookup: {e}",
        )

    # -- L3: adapter-level availability gate --
    if not adapter.check_availability():
        return DiscoveryResult(
            language=language,
            binary=binary,
            available=False,
            error="Server availability check failed",
        )

    # -- Final result --
    if found_path:
        return DiscoveryResult(
            language=language,
            binary=binary,
            available=True,
            path=found_path,
        )
    else:
        return DiscoveryResult(
            language=language,
            binary=binary,
            available=False,
            error=f"Binary '{binary}' not found in PATH",
        )


def discover_all(
    adapters: list[LspLanguageAdapter],
) -> dict[str, DiscoveryResult]:
    """Discover LSP servers for multiple adapters.

    Each adapter is evaluated independently via :func:`discover`.  The
    result dict is keyed by ``adapter.language``.

    Args:
        adapters: A list of concrete :class:`LspLanguageAdapter` instances.

    Returns:
        A ``dict`` mapping each adapter's ``language`` to its
        :class:`DiscoveryResult`.  An empty list returns an empty dict.
    """
    return {adapter.language: discover(adapter) for adapter in adapters}
