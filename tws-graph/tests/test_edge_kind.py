"""Tests for edges/kind.py — EdgeKind enum.

Follows TDD: these tests define the expected behavior before implementation exists.
Referenced from design-p6-edge-inval.md, section "EdgeKind Enum".
"""

import pytest

from tws_graph.edges.kind import EdgeKind


# ---------------------------------------------------------------------------
# 1. 20 members — exact membership
# ---------------------------------------------------------------------------

EXPECTED_MEMBERS: set[str] = {
    # 现有 6 种 (tree-sitter)
    "CALLS",
    "IMPORTS",
    "REFERENCES",
    "EXTENDS",
    "IMPLEMENTS",
    "CONTAINS",
    # P7 数据流分析
    "DATA_FLOWS",
    "READS",
    "WRITES",
    "THROWS",
    # P7 环境/事件
    "ENV_ACCESSES",
    "EMITS",
    "LISTENS_ON",
    # P10 跨服务
    "HTTP_CALLS",
    "GRPC_SERVICE",
    "GRPC_CLIENT",
    "GRPC_SERVER",
    # P4/P8 相似度
    "SIMILAR_TO",
    # P9 分析
    "TEST_EDGE",
    "CONFIG_LINK",
    # P26 v5.2.0 结构边
    "OVERRIDES",
    "INSTANTIATES",
    "DECORATES",
    "TYPE_REF",
    # v7.5 FFI 跨语言追踪
    "CROSS_FFI",
}


EXPECTED_VALUES: dict[str, str] = {
    "CALLS": "calls",
    "IMPORTS": "imports",
    "REFERENCES": "references",
    "EXTENDS": "extends",
    "IMPLEMENTS": "implements",
    "CONTAINS": "contains",
    "DATA_FLOWS": "data_flows",
    "READS": "reads",
    "WRITES": "writes",
    "THROWS": "throws",
    "ENV_ACCESSES": "env_accesses",
    "EMITS": "emits",
    "LISTENS_ON": "listens_on",
    "HTTP_CALLS": "http_calls",
    "GRPC_SERVICE": "grpc_service",
    "GRPC_CLIENT": "grpc_client",
    "GRPC_SERVER": "grpc_server",
    "SIMILAR_TO": "similar_to",
    "TEST_EDGE": "test_edge",
    "CONFIG_LINK": "config_link",
    "OVERRIDES": "overrides",
    "INSTANTIATES": "instantiates",
    "DECORATES": "decorates",
    "TYPE_REF": "type_ref",
    "CROSS_FFI": "cross_ffi",
}


class TestMembers:
    """Verify all 25 members exist with correct values."""

    def test_exactly_25_members(self):
        assert len(EdgeKind) == 25

    def test_all_expected_names_present(self):
        actual = set(EdgeKind.__members__.keys())
        missing = EXPECTED_MEMBERS - actual
        extra = actual - EXPECTED_MEMBERS
        assert not missing, f"Missing members: {missing}"
        assert not extra, f"Unexpected members: {extra}"

    @pytest.mark.parametrize("name,expected_value", EXPECTED_VALUES.items())
    def test_member_value(self, name: str, expected_value: str):
        member = EdgeKind[name]
        assert member.value == expected_value
        # member itself is the value (str inheritance)
        assert member == expected_value

    def test_no_duplicate_values(self):
        """All 25 values must be unique."""
        values = [m.value for m in EdgeKind]
        assert len(values) == len(set(values))

    def test_edge_kind_cross_ffi_value(self):
        """Verify EdgeKind.CROSS_FFI.value == "cross_ffi"."""
        assert EdgeKind.CROSS_FFI.value == "cross_ffi"
        assert EdgeKind.CROSS_FFI == "cross_ffi"
        assert EdgeKind("cross_ffi") is EdgeKind.CROSS_FFI


# ---------------------------------------------------------------------------
# 2. str inheritance — EdgeKind.CALLS == "calls"
# ---------------------------------------------------------------------------

class TestStrInheritance:
    """str(Enum) 基类确保成员可直接当字符串使用."""

    def test_calls_equals_string(self):
        assert EdgeKind.CALLS == "calls"

    def test_imports_equals_string(self):
        assert EdgeKind.IMPORTS == "imports"

    def test_all_members_equal_their_value_strings(self):
        for member in EdgeKind:
            assert member == member.value

    def test_isinstance_str(self):
        assert isinstance(EdgeKind.CALLS, str)

    def test_str_conversion(self):
        assert str(EdgeKind.CALLS) == "calls"
        assert str(EdgeKind.DATA_FLOWS) == "data_flows"

    def test_hash_equals_string_hash(self):
        """Enum member hashes to same value as the underlying string."""
        assert hash(EdgeKind.CALLS) == hash("calls")
        assert hash(EdgeKind.HTTP_CALLS) == hash("http_calls")

    def test_can_be_dict_key_and_lookup_by_string(self):
        """EdgeKind member can serve as a dict key, and lookup by string works."""
        d = {EdgeKind.CALLS: "call_edge", EdgeKind.IMPORTS: "import_edge"}
        assert d[EdgeKind.CALLS] == "call_edge"
        # string key also works because hash equality
        assert d["calls"] == "call_edge"


# ---------------------------------------------------------------------------
# 3. from_str() classmethod — string → EdgeKind
# ---------------------------------------------------------------------------

class TestFromStr:
    """转换函数 from_str(value) 将存储层字符串映射回枚举."""

    @pytest.mark.parametrize(
        "s,expected",
        [
            ("calls", EdgeKind.CALLS),
            ("imports", EdgeKind.IMPORTS),
            ("references", EdgeKind.REFERENCES),
            ("extends", EdgeKind.EXTENDS),
            ("implements", EdgeKind.IMPLEMENTS),
            ("contains", EdgeKind.CONTAINS),
            ("data_flows", EdgeKind.DATA_FLOWS),
            ("reads", EdgeKind.READS),
            ("writes", EdgeKind.WRITES),
            ("throws", EdgeKind.THROWS),
            ("env_accesses", EdgeKind.ENV_ACCESSES),
            ("emits", EdgeKind.EMITS),
            ("listens_on", EdgeKind.LISTENS_ON),
            ("http_calls", EdgeKind.HTTP_CALLS),
            ("grpc_service", EdgeKind.GRPC_SERVICE),
            ("grpc_client", EdgeKind.GRPC_CLIENT),
            ("grpc_server", EdgeKind.GRPC_SERVER),
            ("similar_to", EdgeKind.SIMILAR_TO),
            ("test_edge", EdgeKind.TEST_EDGE),
            ("config_link", EdgeKind.CONFIG_LINK),
            ("cross_ffi", EdgeKind.CROSS_FFI),
        ],
    )
    def test_from_str_valid(self, s: str, expected: EdgeKind):
        result = EdgeKind.from_str(s)
        assert result is expected
        assert isinstance(result, EdgeKind)

    def test_from_str_returns_edgekind_instance(self):
        result = EdgeKind.from_str("calls")
        assert isinstance(result, EdgeKind)
        assert result == EdgeKind.CALLS

    def test_from_str_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="'nonexistent' is not a valid EdgeKind"):
            EdgeKind.from_str("nonexistent")

    def test_from_str_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError):
            EdgeKind.from_str("")

    def test_from_str_case_sensitive(self):
        """字符串必须精确匹配，大小写敏感."""
        with pytest.raises(ValueError):
            EdgeKind.from_str("CALLS")
        with pytest.raises(ValueError):
            EdgeKind.from_str("Calls")


# ---------------------------------------------------------------------------
# 4. Direct construction — EdgeKind("imports")  ← 兼容数据库读出
# ---------------------------------------------------------------------------

class TestDirectConstruction:
    """EdgeKind(s) 直接从字符串构造，无需显式 from_str()."""

    def test_construct_with_valid_string(self):
        assert EdgeKind("imports") is EdgeKind.IMPORTS

    @pytest.mark.parametrize(
        "s,expected",
        [
            ("calls", EdgeKind.CALLS),
            ("references", EdgeKind.REFERENCES),
            ("extends", EdgeKind.EXTENDS),
            ("data_flows", EdgeKind.DATA_FLOWS),
        ],
    )
    def test_construct_various(self, s: str, expected: EdgeKind):
        assert EdgeKind(s) is expected

    def test_construct_with_unknown_string_raises_valueerror(self):
        with pytest.raises(ValueError):
            EdgeKind("nonexistent")

    def test_construct_with_enum_member_returns_self(self):
        """传入 EdgeKind 自身应该返回同一个值."""
        assert EdgeKind(EdgeKind.CALLS) is EdgeKind.CALLS


# ---------------------------------------------------------------------------
# 5. Iteration / membership / in-operator
# ---------------------------------------------------------------------------

class TestIterationAndMembership:
    """枚举的迭代和成员检查."""

    def test_iter_all_members(self):
        members = list(EdgeKind)
        assert len(members) == 25

    def test_contains_by_name(self):
        assert "CALLS" in EdgeKind.__members__

    def test_contains_by_value(self):
        """str(Enum) 不支持 __contains__ with values; 此测试确认语义."""
        # str Enum 的 __contains__ 行为与标准 Enum 相同
        # 标准 Enum: 'X' in MyEnum 等价于 'X' in MyEnum.__members__
        # 不测试 'calls' in EdgeKind，它检查的是成员名
        pass


# ---------------------------------------------------------------------------
# 6. Type narrowing — EdgeKind 可同时用于类型注解
# ---------------------------------------------------------------------------

class TestTypeAnnotations:
    """EdgeKind 可用于类型注解."""

    def test_edgekind_annotation_accepts_member(self):
        """运行时验证类型匹配."""
        kind: EdgeKind = EdgeKind.CALLS
        assert kind == "calls"

    def test_from_str_returns_edgekind(self):
        """类型检查可追踪 from_str 返回 EdgeKind."""
        kind: EdgeKind = EdgeKind.from_str("references")
        assert kind is EdgeKind.REFERENCES
