"""Tests for pass_interface.py — Pass ABC and PipelineContext."""

import pytest
from abc import ABC

from tws_graph.pipeline.pass_interface import Pass, PipelineContext


# ============================================================================
# PipelineContext tests
# ============================================================================


class TestPipelineContextDefaults:
    """PipelineContext should be instantiable with zero arguments."""

    def test_default_construction(self):
        ctx = PipelineContext()
        assert ctx.root_dir == ""
        assert ctx.files == []
        assert ctx.force is False
        assert ctx.store is None
        assert ctx.parsed_results == {}
        assert ctx.errors == []
        assert ctx.pass_timings == {}
        assert ctx.abort is False
        assert ctx.metadata == {}

    def test_resolve_stats_default(self):
        ctx = PipelineContext()
        assert ctx.resolve_stats == {
            "resolved": 0,
            "unresolved": 0,
            "ambiguous": 0,
        }

    def test_mutable_defaults_are_independent(self):
        """Each instance must get its own list/dict — no shared mutable defaults."""
        ctx1 = PipelineContext()
        ctx2 = PipelineContext()
        ctx1.files.append("a.py")
        ctx1.errors.append({"msg": "err"})
        ctx1.pass_timings["stat-filter"] = 1.0
        ctx1.metadata["key"] = "val"
        ctx1.parsed_results["a.py"] = [{"id": "x"}]
        ctx1.resolve_stats["resolved"] = 5

        assert ctx2.files == []
        assert ctx2.errors == []
        assert ctx2.pass_timings == {}
        assert ctx2.metadata == {}
        assert ctx2.parsed_results == {}
        assert ctx2.resolve_stats == {"resolved": 0, "unresolved": 0, "ambiguous": 0}


class TestPipelineContextCustom:
    """PipelineContext should accept custom values for all fields."""

    def test_files_field(self):
        ctx = PipelineContext(files=["a.py", "b.py"])
        assert ctx.files == ["a.py", "b.py"]

    def test_root_dir_field(self):
        ctx = PipelineContext(root_dir="/project")
        assert ctx.root_dir == "/project"

    def test_force_field(self):
        ctx = PipelineContext(force=True)
        assert ctx.force is True

    def test_parsed_results_field(self):
        data = {"f.py": [{"id": "n1", "kind": "function"}]}
        ctx = PipelineContext(parsed_results=data)
        assert ctx.parsed_results == data

    def test_resolve_stats_custom(self):
        ctx = PipelineContext(resolve_stats={"resolved": 3, "unresolved": 1, "ambiguous": 2})
        assert ctx.resolve_stats["resolved"] == 3
        assert ctx.resolve_stats["unresolved"] == 1
        assert ctx.resolve_stats["ambiguous"] == 2

    def test_errors_field(self):
        ctx = PipelineContext(errors=[{"pass": "test", "msg": "err"}])
        assert len(ctx.errors) == 1

    def test_pass_timings_field(self):
        ctx = PipelineContext(pass_timings={"parse": 1.5})
        assert ctx.pass_timings["parse"] == 1.5

    def test_abort_field(self):
        ctx = PipelineContext(abort=True)
        assert ctx.abort is True

    def test_metadata_field(self):
        ctx = PipelineContext(metadata={"version": "1.0"})
        assert ctx.metadata["version"] == "1.0"


class TestPipelineContextTypeAnnotations:
    """Verify field types match the specification."""

    def test_pass_timings_is_str_to_float(self):
        ctx = PipelineContext()
        ctx.pass_timings["stat-filter"] = 0.123
        assert isinstance(ctx.pass_timings["stat-filter"], float)

    def test_resolve_stats_values_are_int(self):
        ctx = PipelineContext()
        for v in ctx.resolve_stats.values():
            assert isinstance(v, int)

    def test_files_is_list_of_str(self):
        ctx = PipelineContext(files=["a.py", "b.py"])
        assert all(isinstance(f, str) for f in ctx.files)

    def test_errors_is_list_of_dict(self):
        ctx = PipelineContext(errors=[{"key": "val"}])
        assert all(isinstance(e, dict) for e in ctx.errors)


class TestPipelineContextEquality:
    """PipelineContext supports equality comparison (dataclass default)."""

    def test_equal_empty(self):
        assert PipelineContext() == PipelineContext()

    def test_not_equal_different_field(self):
        assert PipelineContext(files=["a.py"]) != PipelineContext(files=["b.py"])

    def test_not_equal_resolve_stats(self):
        a = PipelineContext(resolve_stats={"resolved": 0, "unresolved": 0, "ambiguous": 0})
        b = PipelineContext(resolve_stats={"resolved": 1, "unresolved": 0, "ambiguous": 0})
        assert a != b


# ============================================================================
# Pass ABC tests
# ============================================================================


class TestPassInstantiation:
    """Pass is an ABC and cannot be instantiated directly."""

    def test_cannot_instantiate_abstract_pass(self):
        with pytest.raises(TypeError):
            Pass()  # type: ignore[abstract]

    def test_can_instantiate_concrete_subclass(self):
        class MyPass(Pass):
            name = "my-pass"
            description = "A test pass"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        p = MyPass()
        assert p.name == "my-pass"
        assert p.description == "A test pass"

    def test_subclass_must_implement_run(self):
        """A subclass that does not implement run() cannot be instantiated."""
        with pytest.raises(TypeError):

            class BadPass(Pass):  # type: ignore[abstract]
                name = "bad"
                description = "missing run"

            BadPass()  # type: ignore[abstract]


class TestPassRun:
    """Concrete subclasses invoke run() and return PipelineContext."""

    def test_run_returns_pipeline_context(self):
        class SimplePass(Pass):
            name = "simple"
            description = "simple pass"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                ctx.files.append("processed.txt")
                return ctx

        ctx = PipelineContext(files=["input.txt"])
        result = SimplePass().run(ctx)
        assert result is ctx
        assert "processed.txt" in result.files

    def test_run_receives_and_returns_same_instance(self):
        class IdentityPass(Pass):
            name = "identity"
            description = "identity pass"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        ctx = PipelineContext()
        result = IdentityPass().run(ctx)
        assert result is ctx


class TestPassEnabled:
    """enabled() defaults to True; subclasses can override."""

    def test_default_enabled(self):
        class DefaultPass(Pass):
            name = "default"
            description = "default pass"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        ctx = PipelineContext()
        assert DefaultPass().enabled(ctx) is True

    def test_override_enabled_to_false(self):
        class DisabledPass(Pass):
            name = "disabled"
            description = "always disabled"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

            def enabled(self, ctx: PipelineContext) -> bool:
                return False

        ctx = PipelineContext()
        assert DisabledPass().enabled(ctx) is False

    def test_enabled_receives_context(self):
        class ConditionalPass(Pass):
            name = "conditional"
            description = "conditional pass"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

            def enabled(self, ctx: PipelineContext) -> bool:
                return not ctx.abort

        assert ConditionalPass().enabled(PipelineContext(abort=False)) is True
        assert ConditionalPass().enabled(PipelineContext(abort=True)) is False


class TestPassDependencies:
    """dependencies defaults to empty list."""

    def test_default_dependencies(self):
        class NoDepPass(Pass):
            name = "no-dep"
            description = "no dependencies"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        assert NoDepPass.dependencies == []

    def test_custom_dependencies(self):
        class WithDepPass(Pass):
            name = "with-dep"
            description = "has dependencies"
            dependencies = ["stat-filter", "parse-extract"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        assert WithDepPass.dependencies == ["stat-filter", "parse-extract"]


class TestPassClassAttributes:
    """Pass subclasses must define name and description."""

    def test_name_is_accessible_on_class(self):
        class NamedPass(Pass):
            name = "test-name"
            description = "test desc"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        assert NamedPass.name == "test-name"
        assert NamedPass().name == "test-name"

    def test_description_is_accessible_on_class(self):
        class DescPass(Pass):
            name = "desc-test"
            description = "A description string"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        assert DescPass.description == "A description string"


class TestPassRunErrors:
    """Pass.run() may raise exceptions — the interface allows it."""

    def test_run_can_raise(self):
        class FailingPass(Pass):
            name = "failing"
            description = "always fails"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                raise RuntimeError("simulated failure")

        with pytest.raises(RuntimeError, match="simulated failure"):
            FailingPass().run(PipelineContext())
