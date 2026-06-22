"""Tests for PipelineEngine — Pass registration, dependency sorting,
execution, error handling, incremental pipeline, and edge cases."""

import pytest

from tws_graph.pipeline.pass_interface import Pass, PipelineContext


# ============================================================================
# Helper Pass subclasses for testing
# ============================================================================


class _BasePass(Pass):
    """Base test pass that implements run()."""

    def run(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


class PassA(_BasePass):
    name = "pass-a"
    description = "First pass"
    dependencies: list[str] = []


class PassB(_BasePass):
    name = "pass-b"
    description = "Depends on pass-a"
    dependencies: list[str] = ["pass-a"]


class PassC(_BasePass):
    name = "pass-c"
    description = "Depends on pass-a and pass-b"
    dependencies: list[str] = ["pass-a", "pass-b"]


class PassD(_BasePass):
    name = "pass-d"
    description = "Depends on pass-b"
    dependencies: list[str] = ["pass-b"]


class PassNoDep(_BasePass):
    name = "no-dep"
    description = "No dependencies"
    dependencies: list[str] = []


class PassDisabled(_BasePass):
    name = "pass-disabled"
    description = "Always disabled"

    def enabled(self, ctx: PipelineContext) -> bool:
        return False


class PassAbort(_BasePass):
    name = "pass-abort"
    description = "Sets ctx.abort = True"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.abort = True
        return ctx


class PassFailing(_BasePass):
    name = "pass-failing"
    description = "Always raises an exception"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        raise RuntimeError("simulated pass failure")


class PassRecording(_BasePass):
    """Records that its run() was called."""

    name = "pass-recording"
    description = "Records execution"

    def __init__(self):
        super().__init__()
        self.was_run = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        self.was_run = True
        ctx.metadata["pass-recording-ran"] = True
        return ctx


class PassIncremental(_BasePass):
    name = "pass-incremental"
    description = "Supports incremental mode"
    supports_incremental: bool = True


class PassFullOnly(_BasePass):
    name = "pass-full-only"
    description = "Full-index only, no incremental"
    supports_incremental: bool = False


class PassMutating(_BasePass):
    """Mutates ctx.files as part of its run."""

    name = "pass-mutating"
    description = "Modifies ctx during run"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.files = ["processed_" + f for f in ctx.files]
        return ctx


class PassConditionalEnabled(_BasePass):
    """Enabled only when ctx.force is True."""

    name = "pass-conditional"
    description = "Conditional on ctx.force"

    def enabled(self, ctx: PipelineContext) -> bool:
        return ctx.force


class PassWithTiming(_BasePass):
    """Pass that executes with a known duration."""

    name = "pass-timing"
    description = "Records timing info"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        import time
        time.sleep(0.01)
        return ctx


# ============================================================================
# Helper to create PipelineEngine (lazy import to avoid circular deps)
# ============================================================================


def _make_engine():
    """Create a new PipelineEngine instance."""
    from tws_graph.pipeline.engine import PipelineEngine
    return PipelineEngine()


# ============================================================================
# Registration tests
# ============================================================================


class TestRegisterPass:
    """Tests for register_pass()."""

    def test_register_single_pass(self):
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        # Should not raise

    def test_register_multiple_passes(self):
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        engine.register_pass(PassC())
        # Should not raise

    def test_register_duplicate_name_raises(self):
        engine = _make_engine()
        engine.register_pass(PassA())
        with pytest.raises(ValueError, match="already registered|已经注册"):
            engine.register_pass(PassA())

    def test_register_same_class_different_instances_raises(self):
        """Two instances of the same Pass class have the same name."""
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        with pytest.raises(ValueError, match="already registered|已经注册"):
            engine.register_pass(PassNoDep())


# ============================================================================
# build_pipeline() tests
# ============================================================================


class TestBuildPipeline:
    """Tests for build_pipeline()."""

    def test_empty_registry_returns_empty(self):
        engine = _make_engine()
        pipeline = engine.build_pipeline()
        assert pipeline == []

    def test_single_pass_no_deps(self):
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        pipeline = engine.build_pipeline()
        assert len(pipeline) == 1
        assert pipeline[0].name == "no-dep"

    def test_linear_dependency_chain(self):
        """A -> B -> C should sort to A, B, C."""
        engine = _make_engine()
        engine.register_pass(PassC())
        engine.register_pass(PassB())
        engine.register_pass(PassA())
        # Registered out of order, build should sort
        pipeline = engine.build_pipeline()
        names = [p.name for p in pipeline]
        assert names == ["pass-a", "pass-b", "pass-c"], \
            f"Expected ['pass-a', 'pass-b', 'pass-c'] but got {names}"

    def test_diamond_dependency(self):
        """A -> B, A -> C, B -> D, C -> D => A before {B,C} before D."""

        class DiamondD(Pass):
            name = "diamond-d"
            description = "D depends on B and C"
            dependencies: list[str] = ["diamond-b", "diamond-c"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class DiamondC(Pass):
            name = "diamond-c"
            description = "C depends on A"
            dependencies: list[str] = ["diamond-a"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class DiamondB(Pass):
            name = "diamond-b"
            description = "B depends on A"
            dependencies: list[str] = ["diamond-a"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class DiamondA(Pass):
            name = "diamond-a"
            description = "A no deps"
            dependencies: list[str] = []

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        engine = _make_engine()
        engine.register_pass(DiamondD())
        engine.register_pass(DiamondC())
        engine.register_pass(DiamondB())
        engine.register_pass(DiamondA())
        pipeline = engine.build_pipeline()
        names = [p.name for p in pipeline]
        # A must be first
        assert names[0] == "diamond-a", f"Expected diamond-a first, got {names}"
        # B and C must come after A and before D
        assert names.index("diamond-b") < names.index("diamond-d"), \
            f"Expected diamond-b before diamond-d, got {names}"
        assert names.index("diamond-c") < names.index("diamond-d"), \
            f"Expected diamond-c before diamond-d, got {names}"
        assert names.index("diamond-a") < names.index("diamond-b"), \
            f"Expected diamond-a before diamond-b, got {names}"
        assert names.index("diamond-a") < names.index("diamond-c"), \
            f"Expected diamond-a before diamond-c, got {names}"

    def test_multiple_no_dependency_passes(self):
        """Multiple passes with no deps can appear in any order."""
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        engine.register_pass(PassA())
        pipeline = engine.build_pipeline()
        names = {p.name for p in pipeline}
        assert names == {"no-dep", "pass-a"}

    def test_independent_branches(self):
        """A -> B, C -> D (two independent chains)."""
        class PassC2(Pass):
            name = "pass-c2"
            description = "C2"
            dependencies: list[str] = []

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class PassD2(Pass):
            name = "pass-d2"
            description = "D2"
            dependencies: list[str] = ["pass-c2"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        engine.register_pass(PassC2())
        engine.register_pass(PassD2())
        pipeline = engine.build_pipeline()
        names = [p.name for p in pipeline]
        assert names.index("pass-a") < names.index("pass-b")
        assert names.index("pass-c2") < names.index("pass-d2")


class TestBuildPipelineErrors:
    """Error cases for build_pipeline()."""

    def test_circular_dependency_direct(self):
        """A depends on B, B depends on A."""
        class CircularA(Pass):
            name = "circ-a"
            description = "Circular A"
            dependencies: list[str] = ["circ-b"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class CircularB(Pass):
            name = "circ-b"
            description = "Circular B"
            dependencies: list[str] = ["circ-a"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        engine = _make_engine()
        engine.register_pass(CircularA())
        engine.register_pass(CircularB())
        with pytest.raises(ValueError, match=r"circular|循环依赖|cycle"):
            engine.build_pipeline()

    def test_circular_dependency_three_nodes(self):
        """A -> B -> C -> A."""
        class CircA(Pass):
            name = "c-a"
            description = "A"
            dependencies: list[str] = ["c-c"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class CircB(Pass):
            name = "c-b"
            description = "B"
            dependencies: list[str] = ["c-a"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        class CircC(Pass):
            name = "c-c"
            description = "C"
            dependencies: list[str] = ["c-b"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        engine = _make_engine()
        engine.register_pass(CircA())
        engine.register_pass(CircB())
        engine.register_pass(CircC())
        with pytest.raises(ValueError, match=r"circular|循环依赖|cycle"):
            engine.build_pipeline()

    def test_unregistered_dependency_raises(self):
        """Pass depends on a name not in the registry."""
        class BadPass(Pass):
            name = "bad"
            description = "Depends on missing"
            dependencies: list[str] = ["nonexistent"]

            def run(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        engine = _make_engine()
        engine.register_pass(BadPass())
        with pytest.raises(ValueError, match="nonexistent"):
            engine.build_pipeline()


# ============================================================================
# build_incremental_pipeline() tests
# ============================================================================


class TestBuildIncrementalPipeline:
    """Tests for build_incremental_pipeline()."""

    def test_empty_registry_returns_empty(self):
        engine = _make_engine()
        pipeline = engine.build_incremental_pipeline()
        assert pipeline == []

    def test_filters_out_full_only_passes(self):
        engine = _make_engine()
        engine.register_pass(PassIncremental())
        engine.register_pass(PassFullOnly())
        pipeline = engine.build_incremental_pipeline()
        names = [p.name for p in pipeline]
        assert "pass-incremental" in names
        assert "pass-full-only" not in names

    def test_all_passes_default_to_incremental(self):
        """Passes without explicit supports_incremental default to True."""
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        pipeline = engine.build_incremental_pipeline()
        names = [p.name for p in pipeline]
        assert "pass-a" in names
        assert "pass-b" in names

    def test_incremental_pipeline_respects_dependencies(self):
        """Incremental filtering should not break topological ordering."""
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        engine.register_pass(PassC())
        pipeline = engine.build_incremental_pipeline()
        names = [p.name for p in pipeline]
        assert names == ["pass-a", "pass-b", "pass-c"]


# ============================================================================
# execute() tests
# ============================================================================


class TestExecuteBasic:
    """Basic execute() behavior."""

    def test_execute_empty_pipeline_returns_context(self):
        engine = _make_engine()
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert isinstance(ctx, PipelineContext)
        assert ctx.files == []
        assert ctx.root_dir == "/tmp"
        assert ctx.force is False
        assert ctx.errors == []

    def test_execute_single_pass(self):
        engine = _make_engine()
        recorder = PassRecording()
        engine.register_pass(recorder)
        ctx = engine.execute(files=["a.py"], root_dir="/proj")
        assert recorder.was_run is True
        assert ctx.metadata.get("pass-recording-ran") is True

    def test_execute_with_force_flag(self):
        engine = _make_engine()
        engine.register_pass(PassConditionalEnabled())
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=["a.py"], root_dir="/proj", force=True)
        # pass-conditional should run when force=True
        names_run = set(ctx.pass_timings.keys())
        assert "pass-conditional" in names_run

    def test_execute_without_force_skips_conditional(self):
        engine = _make_engine()
        engine.register_pass(PassConditionalEnabled())
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=["a.py"], root_dir="/proj", force=False)
        names_run = set(ctx.pass_timings.keys())
        assert "pass-conditional" not in names_run
        assert "no-dep" in names_run


class TestExecuteOrdering:
    """execute() must respect topological ordering."""

    def test_execute_runs_in_topological_order(self):
        """Verify that the order of execution matches dependency ordering."""
        execution_order = []

        class OrderedA(Pass):
            name = "oa"
            description = "A"
            dependencies: list[str] = []

            def run(self, ctx):
                execution_order.append("oa")
                return ctx

        class OrderedB(Pass):
            name = "ob"
            description = "B"
            dependencies: list[str] = ["oa"]

            def run(self, ctx):
                execution_order.append("ob")
                return ctx

        class OrderedC(Pass):
            name = "oc"
            description = "C"
            dependencies: list[str] = ["ob"]

            def run(self, ctx):
                execution_order.append("oc")
                return ctx

        engine = _make_engine()
        engine.register_pass(OrderedC())
        engine.register_pass(OrderedB())
        engine.register_pass(OrderedA())
        engine.execute(files=[], root_dir="/tmp")
        assert execution_order == ["oa", "ob", "oc"]


class TestExecuteErrorHandling:
    """Continue-on-Error strategy."""

    def test_single_pass_failure_does_not_abort(self):
        engine = _make_engine()
        engine.register_pass(PassFailing())
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=["a.py"], root_dir="/tmp")
        # Both passes should appear in timings/errors
        assert len(ctx.errors) == 1
        assert ctx.errors[0]["pass"] == "pass-failing"
        assert "simulated pass failure" in ctx.errors[0]["error"]
        # no-dep should still run
        assert "no-dep" in ctx.pass_timings

    def test_failed_pass_still_recorded_in_timings(self):
        engine = _make_engine()
        engine.register_pass(PassFailing())
        ctx = engine.execute(files=["a.py"], root_dir="/tmp")
        assert "pass-failing" in ctx.pass_timings
        assert isinstance(ctx.pass_timings["pass-failing"], int)

    def test_error_collected_with_pass_name(self):
        engine = _make_engine()
        engine.register_pass(PassFailing())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert len(ctx.errors) >= 1
        err = ctx.errors[0]
        assert err["pass"] == "pass-failing"
        assert "error" in err
        assert "simulated" in err["error"]

    def test_multiple_failures_all_collected(self):
        engine = _make_engine()
        engine.register_pass(PassFailing())

        class AlsoFailing(Pass):
            name = "also-failing"
            description = "Also fails"
            dependencies: list[str] = []

            def run(self, ctx: PipelineContext) -> PipelineContext:
                raise ValueError("another failure")

        engine.register_pass(AlsoFailing())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert len(ctx.errors) >= 2
        error_passes = {e["pass"] for e in ctx.errors}
        assert "pass-failing" in error_passes
        assert "also-failing" in error_passes


class TestExecuteAbort:
    """ctx.abort = True behavior."""

    def test_abort_stops_subsequent_passes(self):
        recorder_after = PassRecording()
        # Rename to avoid clash with PassRecording
        class AfterAbortPass(Pass):
            name = "after-abort"
            description = "Runs after abort"

            def run(self, ctx: PipelineContext) -> PipelineContext:
                ctx.metadata["after-abort-ran"] = True
                return ctx

        engine = _make_engine()
        engine.register_pass(PassAbort())
        engine.register_pass(AfterAbortPass())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert ctx.abort is True
        # The after-abort pass should NOT have run
        assert "after-abort" not in ctx.pass_timings
        assert ctx.metadata.get("after-abort-ran") is not True

    def test_abort_is_recorded_in_timings(self):
        engine = _make_engine()
        engine.register_pass(PassAbort())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert "pass-abort" in ctx.pass_timings


class TestExecuteEnabledSkip:
    """Pass.enabled(ctx) returning False skips the pass."""

    def test_disabled_pass_is_skipped(self):
        engine = _make_engine()
        engine.register_pass(PassDisabled())
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=[], root_dir="/tmp")
        # pass-disabled should NOT be in timings
        assert "pass-disabled" not in ctx.pass_timings
        assert "no-dep" in ctx.pass_timings

    def test_disabled_pass_not_counted_as_error(self):
        engine = _make_engine()
        engine.register_pass(PassDisabled())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert len([e for e in ctx.errors if e.get("pass") == "pass-disabled"]) == 0


class TestExecuteProgressCallback:
    """progress_callback behavior."""

    def test_callback_called_for_each_pass(self):
        calls = []
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        ctx = engine.execute(
            files=[],
            root_dir="/tmp",
            progress_callback=lambda name, c: calls.append((name, c)),
        )
        assert len(calls) == 2
        names = [c[0] for c in calls]
        assert names == ["pass-a", "pass-b"]
        # Each call should receive the PipelineContext as second arg
        for _, ctx_arg in calls:
            assert isinstance(ctx_arg, PipelineContext)

    def test_callback_not_called_for_skipped_passes(self):
        calls = []
        engine = _make_engine()
        engine.register_pass(PassDisabled())
        engine.register_pass(PassNoDep())
        engine.execute(
            files=[],
            root_dir="/tmp",
            progress_callback=lambda name, c: calls.append(name),
        )
        assert "pass-disabled" not in calls
        assert "no-dep" in calls

    def test_callback_not_called_for_failed_passes(self):
        calls = []
        engine = _make_engine()
        engine.register_pass(PassFailing())
        engine.register_pass(PassNoDep())
        engine.execute(
            files=[],
            root_dir="/tmp",
            progress_callback=lambda name, c: calls.append(name),
        )
        assert "pass-failing" not in calls
        assert "no-dep" in calls

    def test_callback_not_called_for_aborted_passes(self):
        calls = []
        engine = _make_engine()
        engine.register_pass(PassAbort())
        engine.register_pass(PassNoDep())
        engine.execute(
            files=[],
            root_dir="/tmp",
            progress_callback=lambda name, c: calls.append(name),
        )
        assert len(calls) == 1
        assert calls[0] == "pass-abort"


class TestExecuteTimings:
    """Timing information recorded correctly."""

    def test_all_passes_have_timing(self):
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert "pass-a" in ctx.pass_timings
        assert "pass-b" in ctx.pass_timings
        assert ctx.pass_timings["pass-a"] >= 0
        assert ctx.pass_timings["pass-b"] >= 0

    def test_duration_ms_is_set(self):
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert ctx.duration_ms >= 0


class TestExecuteMutatingContext:
    """Passes can modify ctx and changes propagate."""

    def test_mutation_propagates_to_next_pass(self):
        class ReaderPass(Pass):
            name = "reader"
            description = "Reads from ctx"
            dependencies: list[str] = []

            def run(self, ctx: PipelineContext) -> PipelineContext:
                ctx.metadata["reads"] = ctx.files
                return ctx

        engine = _make_engine()
        engine.register_pass(PassMutating())
        engine.register_pass(ReaderPass())
        ctx = engine.execute(files=["a.py", "b.py"], root_dir="/tmp")
        assert ctx.metadata["reads"] == ["processed_a.py", "processed_b.py"]


# ============================================================================
# execute_incremental() tests
# ============================================================================


class TestExecuteIncremental:
    """Tests for execute_incremental()."""

    def test_execute_incremental_basic(self):
        engine = _make_engine()
        engine.register_pass(PassIncremental())
        engine.register_pass(PassNoDep())
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/tmp"
        )
        assert isinstance(ctx, PipelineContext)
        assert ctx.files == ["a.py"]
        assert ctx.root_dir == "/tmp"
        assert ctx.force is False

    def test_execute_incremental_skips_full_only(self):
        engine = _make_engine()
        engine.register_pass(PassIncremental())
        engine.register_pass(PassFullOnly())
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/tmp"
        )
        assert "pass-incremental" in ctx.pass_timings
        assert "pass-full-only" not in ctx.pass_timings

    def test_execute_incremental_respects_abort(self):
        engine = _make_engine()

        class IncAbort(Pass):
            name = "inc-abort"
            description = "Aborts incremental"
            supports_incremental: bool = True

            def run(self, ctx: PipelineContext) -> PipelineContext:
                ctx.abort = True
                return ctx

        engine.register_pass(IncAbort())
        engine.register_pass(PassIncremental())
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/tmp"
        )
        assert "pass-incremental" not in ctx.pass_timings

    def test_execute_incremental_errors_collected(self):
        class IncFailing(Pass):
            name = "inc-failing"
            description = "Fails in incremental"
            supports_incremental: bool = True

            def run(self, ctx: PipelineContext) -> PipelineContext:
                raise RuntimeError("incremental failure")

        engine = _make_engine()
        engine.register_pass(IncFailing())
        engine.register_pass(PassIncremental())
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/tmp"
        )
        assert len(ctx.errors) >= 1
        assert ctx.errors[0]["pass"] == "inc-failing"
        # pass-incremental should still run
        assert "pass-incremental" in ctx.pass_timings


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    """Edge case and boundary condition tests."""

    def test_no_registered_passes_build_pipeline_empty(self):
        engine = _make_engine()
        assert engine.build_pipeline() == []

    def test_no_registered_passes_execute_ok(self):
        engine = _make_engine()
        ctx = engine.execute(files=["a.py"], root_dir="/tmp", force=True)
        assert ctx.errors == []
        assert ctx.pass_timings == {}
        assert ctx.duration_ms >= 0

    def test_no_registered_passes_execute_incremental_ok(self):
        engine = _make_engine()
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/tmp"
        )
        assert ctx.errors == []
        assert ctx.pass_timings == {}
        assert ctx.duration_ms >= 0

    def test_none_callback_does_not_raise(self):
        engine = _make_engine()
        engine.register_pass(PassNoDep())
        ctx = engine.execute(
            files=[], root_dir="/tmp", progress_callback=None
        )
        assert "no-dep" in ctx.pass_timings

    def test_circular_dependency_error_message_mentions_involved_passes(self):
        class X(Pass):
            name = "x"
            description = "X depends on Y"
            dependencies: list[str] = ["y"]

            def run(self, ctx):
                return ctx

        class Y(Pass):
            name = "y"
            description = "Y depends on X"
            dependencies: list[str] = ["x"]

            def run(self, ctx):
                return ctx

        engine = _make_engine()
        engine.register_pass(X())
        engine.register_pass(Y())
        with pytest.raises(ValueError) as exc_info:
            engine.build_pipeline()
        msg = str(exc_info.value).lower()
        assert "circular" in msg or "cycle" in msg or "循环依赖" in msg

    def test_build_pipeline_is_idempotent(self):
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        p1 = engine.build_pipeline()
        p2 = engine.build_pipeline()
        assert [p.name for p in p1] == [p.name for p in p2]

    def test_build_pipeline_does_not_consume_passes(self):
        """Calling build_pipeline should not remove registered passes."""
        engine = _make_engine()
        engine.register_pass(PassA())
        engine.register_pass(PassB())
        engine.build_pipeline()
        # Should still be able to build again
        p2 = engine.build_pipeline()
        assert len(p2) == 2

    def test_store_propagated_to_context(self):
        """If a store is set on the engine, it should be in ctx.store."""
        from tws_graph.pipeline.engine import PipelineEngine

        class FakeStore:
            pass

        store = FakeStore()
        engine = PipelineEngine(store=store)
        engine.register_pass(PassNoDep())
        ctx = engine.execute(files=[], root_dir="/tmp")
        assert ctx.store is store

    def test_execute_with_default_root_dir(self):
        engine = _make_engine()
        ctx = engine.execute(files=[])
        assert ctx.root_dir == "."
        assert ctx.force is False
