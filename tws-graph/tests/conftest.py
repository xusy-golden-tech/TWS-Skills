"""Shared fixtures for tws-graph tests."""

import os
import tempfile
import shutil
import pytest
from pathlib import Path

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


# ---------------------------------------------------------------------------
# fixture file contents
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_py_src() -> str:
    """Return the sample.py fixture content."""
    return """'''Sample module for testing the Python extractor.'''

import math
from typing import List, Optional


def calculateTotal(items: List[float]) -> float:
    \"\"\"Calculate the sum of all items.\"\"\"
    return sum(items)


def _validateInput(value: str) -> bool:
    \"\"\"Private helper: validate input string.\"\""
    return len(value) > 0


class OrderService:
    \"\"\"Service class for order management.\"\""

    def createOrder(self, items: List[str], user_id: int) -> dict:
        \"\"\"Create a new order.\"\"\"
        if not _validateInput(str(user_id)):
            raise ValueError("Invalid user")
        return self._buildOrder(items, user_id)

    def _buildOrder(self, items: List[str], user_id: int) -> dict:
        \"\"\"Internal: build order dict.\"\"\"
        return {"items": items, "user": user_id}


class PaymentHandler:
    \"\"\"Handles payment processing.\"\""

    def process(self, amount: float) -> bool:
        service = OrderService()
        service.createOrder([], 0)
        total = calculateTotal([amount])
        return total > 0
"""


@pytest.fixture(scope="session")
def sample_ts_src() -> str:
    """Return the sample.ts fixture content."""
    return """// Sample module for testing the TypeScript extractor.

import { log } from "./logger";

export function calculateTotal(items: number[]): number {
    return items.reduce((a, b) => a + b, 0);
}

function validateInput(value: string): boolean {
    return value.length > 0;
}

export class OrderService {
    public createOrder(items: string[], userId: number): Record<string, unknown> {
        if (!validateInput(String(userId))) {
            throw new Error("Invalid user");
        }
        return this.buildOrder(items, userId);
    }

    private buildOrder(items: string[], userId: number): Record<string, unknown> {
        return { items, user: userId };
    }
}

export class PaymentHandler {
    public process(amount: number): boolean {
        const service = new OrderService();
        service.createOrder([], 0);
        const total = calculateTotal([amount]);
        return total > 0;
    }
}

export enum OrderStatus {
    Pending,
    Confirmed,
    Shipped,
}

export interface PaymentResult {
    success: boolean;
    transactionId: string;
}
"""


@pytest.fixture(scope="session")
def sample_kt_src() -> str:
    """Return the sample.kt fixture content."""
    return """// Sample module for testing the Kotlin extractor.

package com.example

import kotlin.collections.List

fun calculateTotal(items: List<Double>): Double {
    return items.sum()
}

private fun validateInput(value: String): Boolean {
    return value.isNotEmpty()
}

class OrderService(private val repo: OrderRepository) {

    fun createOrder(items: List<String>, userId: Int): Map<String, Any> {
        if (!validateInput(userId.toString())) {
            throw IllegalArgumentException("Invalid user")
        }
        return buildOrder(items, userId)
    }

    private fun buildOrder(items: List<String>, userId: Int): Map<String, Any> {
        return mapOf("items" to items, "user" to userId)
    }
}

class PaymentHandler {
    fun process(amount: Double): Boolean {
        val service = OrderService(FakeRepo())
        service.createOrder(emptyList(), 0)
        val total = calculateTotal(listOf(amount))
        return total > 0
    }
}

interface OrderRepository {
    fun findById(id: String): List<String>
    fun save(order: Map<String, Any>)
}

data class Item(val name: String, val price: Double)

object Config {
    val apiUrl: String = "https://api.example.com"
}
"""


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return str(tmp_path / "codegraph" / "index.db")


@pytest.fixture
def db_conn(temp_db_path: str):
    """Initialize a fresh DatabaseConnection with schema."""
    conn = DatabaseConnection.initialize(temp_db_path)
    yield conn
    conn.close()


@pytest.fixture
def queries(db_conn: DatabaseConnection) -> QueryBuilder:
    """Create a QueryBuilder instance from a database connection."""
    return QueryBuilder(db_conn.conn)


# ---------------------------------------------------------------------------
# project fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    return tmp_path


@pytest.fixture
def sample_py_project(temp_project: Path, sample_py_src: str) -> Path:
    """Create a project dir with sample.py."""
    fixt_dir = temp_project / "fixtures"
    fixt_dir.mkdir()
    (fixt_dir / "sample.py").write_text(sample_py_src, encoding="utf-8")
    return temp_project


@pytest.fixture
def sample_ts_project(temp_project: Path, sample_ts_src: str) -> Path:
    """Create a project dir with sample.ts."""
    fixt_dir = temp_project / "fixtures"
    fixt_dir.mkdir()
    (fixt_dir / "sample.ts").write_text(sample_ts_src, encoding="utf-8")
    return temp_project


@pytest.fixture
def sample_kt_project(temp_project: Path, sample_kt_src: str) -> Path:
    """Create a project dir with sample.kt."""
    fixt_dir = temp_project / "fixtures"
    fixt_dir.mkdir()
    (fixt_dir / "sample.kt").write_text(sample_kt_src, encoding="utf-8")
    return temp_project


@pytest.fixture(scope="session")
def sample_c_src() -> str:
    """Return the sample.c fixture content."""
    return """#include <stdio.h>
#include <stdlib.h>
#include "mylib.h"

struct Point {
    int x;
    int y;
};

union Data {
    int i;
    float f;
};

enum Color { RED, GREEN, BLUE };

int global_counter = 0;

static int hidden_counter = 0;

int add(int a, int b) {
    return a + b;
}

void print_point(struct Point p) {
    printf("Point(%d, %d)\\n", p.x, p.y);
}

int calculate_total(int x, int y) {
    int temp = add(x, y);
    return temp;
}

int main(void) {
    struct Point p = {1, 2};
    print_point(p);
    int result = add(p.x, p.y);
    int total = calculate_total(p.x, p.y);
    return 0;
}
"""


@pytest.fixture(scope="session")
def sample_cpp_src() -> str:
    """Return the sample.cpp fixture content."""
    return """#include <iostream>
#include <vector>
#include "mylib.hpp"

namespace math {

template<typename T>
class Calculator {
public:
    T add(T a, T b) { return a + b; }
    T multiply(T a, T b) { return a * b; }
};

struct Point {
    double x;
    double y;
};

class Shape {
public:
    virtual double area() const = 0;
    virtual ~Shape() {}
};

class Circle : public Shape {
    double radius;
public:
    Circle(double r) : radius(r) {}
    double area() const override { return 3.14 * radius * radius; }
};

} // namespace math

int global_count = 0;

void helper() {
    std::cout << "helper called" << std::endl;
}

int main() {
    math::Calculator<int> calc;
    int result = calc.add(1, 2);
    helper();
    return 0;
}
"""
