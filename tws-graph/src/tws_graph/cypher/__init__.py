"""Cypher query engine — public API.

Provides the CypherEngine facade and a convenience execute() function
for running Cypher queries against the code graph Store.
"""

from tws_graph.cypher.lexer import Lexer
from tws_graph.cypher.parser import Parser
from tws_graph.cypher.planner import Planner
from tws_graph.cypher.executor import Executor, ResultSet, Row
from tws_graph.cypher.errors import (
    CypherError,
    CypherLexerError,
    CypherSyntaxError,
    CypherSemanticError,
    CypherExecutionError,
)
from tws_graph.store.interface import Store


class CypherEngine:
    """Cypher 查询引擎门面。

    一次性创建，可多次调用 execute()。内部持有 Lexer→Parser→Planner→Executor 流水线。
    Planner 缓存在实例中（无状态，纯 AST→Plan 转换）。Executor 每次新建（绑定 Store）。
    """

    def __init__(self) -> None:
        """创建 CypherEngine 实例。"""
        self._planner = Planner()

    def execute(self, query: str, store: Store) -> ResultSet:
        """执行 Cypher 查询。

        参数:
            query: Cypher 查询字符串（如 ``"MATCH (n) RETURN n"``）
            store: Store 接口实例（SqliteStore 或 MemoryStore）

        返回:
            ResultSet — 包含 columns、rows、total_count

        异常:
            CypherLexerError: 词法错误
            CypherSyntaxError: 语法错误
            CypherSemanticError: 语义错误
            CypherExecutionError: 执行错误
        """
        # 1. Lexer(query) → Token 流
        lexer = Lexer(query)
        # 2. Parser(lexer) → AST Statement
        parser = Parser(lexer)
        statement = parser.parse()
        # 3. Planner().plan(statement) → LogicalPlan
        plan = self._planner.plan(statement)
        # 4. Executor(store).execute(plan) → ResultSet
        executor = Executor(store)
        return executor.execute(plan)


def execute(query: str, store: Store) -> ResultSet:
    """执行 Cypher 查询的便捷函数。

    等价于 ``CypherEngine().execute(query, store)``。

    参数:
        query: Cypher 查询字符串
        store: Store 接口实例

    返回:
        ResultSet
    """
    return CypherEngine().execute(query, store)


__all__ = [
    "CypherEngine",
    "execute",
    "ResultSet",
    "Row",
    "CypherError",
    "CypherLexerError",
    "CypherSyntaxError",
    "CypherSemanticError",
    "CypherExecutionError",
]
