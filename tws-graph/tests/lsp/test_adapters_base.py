"""Tests for lsp/adapters/base.py --- LspLanguageAdapter ABC + ImportInfo.

TDD red phase: these tests are written before the adapters/base module exists.
They encode the acceptance criteria from design-p5-lsp.md for adapters/base.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - ImportInfo dataclass field validation
  - LspLanguageAdapter is ABC, cannot instantiate directly
  - Subclass implementing all abstract methods can be instantiated
  - Subclass missing abstract methods cannot be instantiated
  - language property is accessible on concrete subclass
"""

import abc

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.adapters.base is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.adapters.base import (  # noqa: E402 (expected ImportError in red phase)
    ImportInfo,
    LspLanguageAdapter,
)


# ============================================================================
# ImportInfo dataclass tests
# ============================================================================

class TestImportInfo:
    """ImportInfo(module, symbol, is_relative, alias)"""

    def test_create_basic(self):
        """ImportInfo all fields with simple values."""
        info = ImportInfo(module="os", symbol="path", is_relative=False, alias=None)
        assert info.module == "os"
        assert info.symbol == "path"
        assert info.is_relative is False
        assert info.alias is None

    def test_create_with_alias(self):
        """ImportInfo: alias field is populated for as-imports like `import numpy as np`."""
        info = ImportInfo(module="numpy", symbol="numpy", is_relative=False, alias="np")
        assert info.alias == "np"

    def test_create_relative_import(self):
        """ImportInfo: is_relative=True for relative imports like `from . import foo`."""
        info = ImportInfo(module=".", symbol="sibling", is_relative=True, alias=None)
        assert info.module == "."
        assert info.symbol == "sibling"
        assert info.is_relative is True

    def test_equality(self):
        """ImportInfo: dataclass equality by value."""
        a = ImportInfo(module="foo", symbol="Bar", is_relative=False, alias=None)
        b = ImportInfo(module="foo", symbol="Bar", is_relative=False, alias=None)
        c = ImportInfo(module="foo", symbol="Baz", is_relative=False, alias=None)
        assert a == b
        assert a != c

    def test_defaults(self):
        """ImportInfo should require all four fields (no defaults per spec)."""
        with pytest.raises(TypeError):
            ImportInfo()


# ============================================================================
# LspLanguageAdapter ABC tests
# ============================================================================

class TestLspLanguageAdapterIsAbstract:
    """Verify LspLanguageAdapter is a proper ABC that enforces the interface."""

    def test_is_abc(self):
        """LspLanguageAdapter should be an ABC."""
        assert issubclass(LspLanguageAdapter, abc.ABC)

    def test_cannot_instantiate_directly(self):
        """Cannot instantiate LspLanguageAdapter without concrete implementations."""
        with pytest.raises(TypeError):
            LspLanguageAdapter()

    def test_abstract_methods_defined(self):
        """All five abstract methods must be declared on the ABC."""
        expected_abstract_methods = {
            "get_server_command",
            "parse_import",
            "resolve_module_path",
            "get_file_extensions",
            "check_availability",
        }
        actual_abstract_methods = {
            name for name, method in LspLanguageAdapter.__abstractmethods__.items()
        } if hasattr(LspLanguageAdapter, '__abstractmethods__') else set()

        # The ABC mechanism tracks these differently between Python versions.
        # Validate via __abstractmethods__ if available, otherwise via instantiation
        # tests below.
        if hasattr(LspLanguageAdapter, '__abstractmethods__'):
            for method in expected_abstract_methods:
                assert method in LspLanguageAdapter.__abstractmethods__, (
                    f"{method} should be an abstract method of LspLanguageAdapter"
                )

    def test_has_language_property(self):
        """LspLanguageAdapter should declare a language property/attribute."""
        # Check that the class has language as a required attribute (via annotations or ABCMeta)
        annotations = getattr(LspLanguageAdapter, '__annotations__', {})
        assert 'language' in annotations, (
            "LspLanguageAdapter should have a 'language' type annotation"
        )


# ============================================================================
# Subclass instantiation tests
# ============================================================================

class TestLspLanguageAdapterSubclass:
    """Verify that subclasses are correctly validated by ABC."""

    def _make_concrete_subclass(self):
        """Create a minimal concrete subclass implementing all abstract methods."""
        class ConcreteAdapter(LspLanguageAdapter):
            language = "test-lang"

            def get_server_command(self, workspace_root):
                return ["test-server", "--stdio"]

            def parse_import(self, import_statement, current_file):
                return ImportInfo(
                    module=import_statement,
                    symbol="*",
                    is_relative=False,
                    alias=None,
                )

            def resolve_module_path(self, module_name, current_file):
                return "/fake/path"

            def get_file_extensions(self):
                return [".test"]

            def check_availability(self):
                return True

        return ConcreteAdapter

    def test_concrete_subclass_can_instantiate(self):
        """A subclass implementing ALL abstract methods can be instantiated."""
        Concrete = self._make_concrete_subclass()
        instance = Concrete()
        assert isinstance(instance, LspLanguageAdapter)
        assert isinstance(instance, Concrete)

    def test_concrete_subclass_has_language(self):
        """Concrete subclass must define language."""
        Concrete = self._make_concrete_subclass()
        instance = Concrete()
        assert instance.language == "test-lang"

    def test_concrete_subclass_methods_work(self):
        """Concrete subclass methods return expected values."""
        Concrete = self._make_concrete_subclass()
        instance = Concrete()
        assert instance.get_server_command("/ws") == ["test-server", "--stdio"]
        assert instance.get_file_extensions() == [".test"]
        assert instance.check_availability() is True

    def test_subclass_missing_parse_import_cannot_instantiate(self):
        """Missing parse_import -> TypeError on instantiation."""

        class PartialAdapter(LspLanguageAdapter):
            language = "partial"

            def get_server_command(self, workspace_root):
                return ["cmd"]

            # parse_import NOT implemented
            def resolve_module_path(self, module_name, current_file):
                return "/p"

            def get_file_extensions(self):
                return [".x"]

            def check_availability(self):
                return True

        with pytest.raises(TypeError):
            PartialAdapter()

    def test_subclass_missing_get_server_command_cannot_instantiate(self):
        """Missing get_server_command -> TypeError on instantiation."""

        class PartialAdapter(LspLanguageAdapter):
            language = "partial"

            # get_server_command NOT implemented
            def parse_import(self, import_statement, current_file):
                return ImportInfo(module="x", symbol="y", is_relative=False, alias=None)

            def resolve_module_path(self, module_name, current_file):
                return "/p"

            def get_file_extensions(self):
                return [".x"]

            def check_availability(self):
                return True

        with pytest.raises(TypeError):
            PartialAdapter()

    def test_subclass_missing_resolve_module_path_cannot_instantiate(self):
        """Missing resolve_module_path -> TypeError on instantiation."""

        class PartialAdapter(LspLanguageAdapter):
            language = "partial"

            def get_server_command(self, workspace_root):
                return ["cmd"]

            def parse_import(self, import_statement, current_file):
                return ImportInfo(module="x", symbol="y", is_relative=False, alias=None)

            # resolve_module_path NOT implemented
            def get_file_extensions(self):
                return [".x"]

            def check_availability(self):
                return True

        with pytest.raises(TypeError):
            PartialAdapter()

    def test_subclass_missing_get_file_extensions_cannot_instantiate(self):
        """Missing get_file_extensions -> TypeError on instantiation."""

        class PartialAdapter(LspLanguageAdapter):
            language = "partial"

            def get_server_command(self, workspace_root):
                return ["cmd"]

            def parse_import(self, import_statement, current_file):
                return ImportInfo(module="x", symbol="y", is_relative=False, alias=None)

            def resolve_module_path(self, module_name, current_file):
                return "/p"

            # get_file_extensions NOT implemented
            def check_availability(self):
                return True

        with pytest.raises(TypeError):
            PartialAdapter()

    def test_subclass_missing_check_availability_cannot_instantiate(self):
        """Missing check_availability -> TypeError on instantiation."""

        class PartialAdapter(LspLanguageAdapter):
            language = "partial"

            def get_server_command(self, workspace_root):
                return ["cmd"]

            def parse_import(self, import_statement, current_file):
                return ImportInfo(module="x", symbol="y", is_relative=False, alias=None)

            def resolve_module_path(self, module_name, current_file):
                return "/p"

            def get_file_extensions(self):
                return [".x"]

            # check_availability NOT implemented

        with pytest.raises(TypeError):
            PartialAdapter()
