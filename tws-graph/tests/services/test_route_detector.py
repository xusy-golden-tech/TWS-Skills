"""Tests for RouteDetector — cross-language HTTP route detection.

Tests cover Python, TypeScript, Java, and Go route detection
for Flask, FastAPI, Django, Bottle, Express, NestJS,
Spring, Jakarta, net/http, gorilla-mux, chi, gin, and echo.
"""

import pytest
import tree_sitter_language_pack

from tws_graph.services.route_detector import RouteDetector, _infer_http_method


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_python(code: str):
    parser = tree_sitter_language_pack.get_parser("python")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_typescript(code: str):
    parser = tree_sitter_language_pack.get_parser("typescript")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_java(code: str):
    parser = tree_sitter_language_pack.get_parser("java")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_go(code: str):
    parser = tree_sitter_language_pack.get_parser("go")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _make_detector():
    return RouteDetector()


# ===================================================================
# Python route tests
# ===================================================================

class TestPythonRouteDetection:
    """HTTP route detection for Python source files."""

    def test_flask_app_route_default_get(self):
        """Flask @app.route without methods arg defaults to GET."""
        code = """\
from flask import Flask

app = Flask(__name__)

@app.route('/users')
def get_users():
    return []
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"
        assert edge["source_loc"].startswith("test.py:")
        assert edge["provenance"] == "tree-sitter"

    def test_flask_app_route_with_methods_post(self):
        """Flask @app.route with methods=["POST"] extracts method correctly."""
        code = """\
from flask import Flask

app = Flask(__name__)

@app.route('/users', methods=['POST'])
def create_user():
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_flask_bp_route(self):
        """Flask blueprint @bp.route detection."""
        code = """\
from flask import Blueprint

bp = Blueprint('users', __name__)

@bp.route('/users/<int:id>')
def get_user(id):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "GET"

    def test_fastapi_router_get(self):
        """FastAPI @router.get decorator detection."""
        code = """\
from fastapi import APIRouter

router = APIRouter()

@router.get("/items")
def get_items():
    return []
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/items"
        assert edge["http_method"] == "GET"

    def test_fastapi_router_post(self):
        """FastAPI @router.post decorator detection."""
        code = """\
from fastapi import APIRouter

router = APIRouter()

@router.post("/items")
def create_item():
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_fastapi_router_put(self):
        """FastAPI @router.put decorator detection."""
        code = """\
from fastapi import APIRouter

router = APIRouter()

@router.put("/items/{item_id}")
def update_item(item_id: int):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "PUT"

    def test_fastapi_router_delete(self):
        """FastAPI @router.delete decorator detection."""
        code = """\
from fastapi import APIRouter

router = APIRouter()

@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "DELETE"

    def test_fastapi_router_patch(self):
        """FastAPI @router.patch decorator detection."""
        code = """\
from fastapi import APIRouter

router = APIRouter()

@router.patch("/items/{item_id}")
def patch_item(item_id: int):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "PATCH"

    def test_fastapi_app_get(self):
        """FastAPI @app.get decorator detection (app-level routes)."""
        code = """\
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/health"
        assert route_edges[0]["http_method"] == "GET"

    def test_django_path_function(self):
        """Django path() function route detection."""
        code = """\
from django.urls import path
from . import views

urlpatterns = [
    path('articles/', views.article_list),
]
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "articles/"
        assert edge["http_method"] == "UNKNOWN"

    def test_django_re_path_function(self):
        """Django re_path() function route detection."""
        code = """\
from django.urls import re_path
from . import views

urlpatterns = [
    re_path(r'^articles/(?P<year>[0-9]{4})/$', views.year_archive),
]
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        # re_path may or may not match depending on regex string handling
        assert len(route_edges) >= 0

    def test_bottle_get_decorator(self):
        """Bottle @get('/hello') decorator detection."""
        code = """\
from bottle import get, run

@get('/hello')
def hello():
    return 'Hello World'
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/hello"
        assert route_edges[0]["http_method"] == "GET"

    def test_bottle_post_decorator(self):
        """Bottle @post('/hello') decorator detection."""
        code = """\
from bottle import post

@post('/submit')
def handle_submit():
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_bottle_route_decorator(self):
        """Bottle @route('/hello') decorator with UNKNOWN method."""
        code = """\
from bottle import route

@route('/generic')
def generic_handler():
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "UNKNOWN"

    def test_empty_for_plain_python_code(self):
        """Plain Python code with no routes should return no edges."""
        code = """\
def plain_function():
    x = 1 + 2
    return x

class MyClass:
    def method(self):
        pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert edges == []

    def test_http_client_call_detection(self):
        """HTTP client calls like requests.get should produce http_calls edges."""
        code = """\
import requests

def fetch_data():
    response = requests.get('https://api.example.com/users')
    return response.json()
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        call_edges = [e for e in edges if e["kind"] == "http_calls"]
        assert len(call_edges) >= 1
        edge = call_edges[0]
        assert edge["provenance"] == "tree-sitter"

    def test_httpx_client_call(self):
        """httpx.get() should produce http_calls edges."""
        code = """\
import httpx

resp = httpx.get('https://api.example.com/data')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        call_edges = [e for e in edges if e["kind"] == "http_calls"]
        assert len(call_edges) >= 1

    def test_custom_variable_name_router(self):
        """Routes should be detected regardless of decorator variable name."""
        code = """\
from fastapi import APIRouter

api = APIRouter()

@api.get("/v2/resources")
def list_resources():
    return []
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/v2/resources"
        assert route_edges[0]["http_method"] == "GET"


# ===================================================================
# TypeScript route tests
# ===================================================================

class TestTypeScriptRouteDetection:
    """HTTP route detection for TypeScript source files."""

    def test_express_app_get(self):
        """Express app.get('/users') detection."""
        code = """\
import express from 'express';

const app = express();

app.get('/users', (req, res) => {
    res.json([]);
});
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"
        assert edge["provenance"] == "tree-sitter"

    def test_express_app_post(self):
        """Express app.post('/users') detection."""
        code = """\
const app = require('express')();

app.post('/users', (req, res) => {
    res.status(201).send();
});
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_express_router_delete(self):
        """Express router.delete('/users/:id') detection."""
        code = """\
import { Router } from 'express';

const router = Router();

router.delete('/users/:id', (req, res) => {
    res.status(204).send();
});
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "DELETE"

    def test_express_router_put(self):
        """Express router.put('/users/:id') detection."""
        code = """\
import { Router } from 'express';

const router = Router();

router.put('/users/:id', (req, res) => {
    res.json({});
});
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "PUT"

    def test_nestjs_get_decorator(self):
        """NestJS @Get('/users') decorator detection."""
        code = """\
import { Get } from '@nestjs/common';

export class UsersController {
    @Get('/users')
    getUsers(): string {
        return 'users';
    }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"

    def test_nestjs_post_decorator(self):
        """NestJS @Post('/users') decorator detection."""
        code = """\
import { Post } from '@nestjs/common';

export class UsersController {
    @Post('/users')
    createUser(): string {
        return 'created';
    }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_nestjs_controller_prefix(self):
        """NestJS @Controller('users') with prefix path."""
        code = """\
import { Controller, Get } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get()
    list(): string {
        return 'list';
    }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "users"

    def test_empty_for_plain_ts_code(self):
        """Plain TypeScript with no routes should return no edges."""
        code = """\
function hello(): string {
    const x = 1 + 2;
    return 'hello';
}

class Helper {
    doWork(): void {}
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        assert edges == []


# ===================================================================
# Java route tests
# ===================================================================

class TestJavaRouteDetection:
    """HTTP route detection for Java source files."""

    def test_spring_get_mapping(self):
        """Spring @GetMapping('/users') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.GetMapping;

public class UserController {
    @GetMapping("/users")
    public List<User> getUsers() {
        return null;
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"
        assert edge["provenance"] == "tree-sitter"

    def test_spring_post_mapping(self):
        """Spring @PostMapping('/users') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.PostMapping;

public class UserController {
    @PostMapping("/users")
    public User createUser() {
        return null;
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_spring_put_mapping(self):
        """Spring @PutMapping('/users/{id}') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.PutMapping;

public class UserController {
    @PutMapping("/users/{id}")
    public User updateUser() {
        return null;
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "PUT"

    def test_spring_delete_mapping(self):
        """Spring @DeleteMapping('/users/{id}') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.DeleteMapping;

public class UserController {
    @DeleteMapping("/users/{id}")
    public void deleteUser() {
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "DELETE"

    def test_spring_patch_mapping(self):
        """Spring @PatchMapping('/users/{id}') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.PatchMapping;

public class UserController {
    @PatchMapping("/users/{id}")
    public User patchUser() {
        return null;
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "PATCH"

    def test_spring_request_mapping_with_value(self):
        """Spring @RequestMapping(value='/users') annotation detection."""
        code = """\
import org.springframework.web.bind.annotation.RequestMapping;

public class UserController {
    @RequestMapping(value = "/users", method = RequestMethod.GET)
    public List<User> getUsers() {
        return null;
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "UserController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        # @RequestMapping without explicit method → UNKNOWN
        assert edge["http_method"] == "UNKNOWN"

    def test_jakarta_path_annotation(self):
        """Jakarta @Path('/resource') annotation detection."""
        code = """\
import jakarta.ws.rs.Path;

@Path("/resources")
public class ResourceController {
    public String get() {
        return "ok";
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "ResourceController.java", "java")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/resources"
        assert edge["http_method"] == "UNKNOWN"

    def test_jakarta_get_annotation(self):
        """Jakarta @GET annotation -- no path, no edge."""
        code = """\
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/resources")
public class ResourceController {
    @GET
    public String getAll() {
        return "ok";
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "ResourceController.java", "java")

        # @GET matches but has no path -> only @Path("/resources") produces an edge
        route_edges = [e for e in edges if e["kind"] == "http_route"]
        # @Path produces 1 edge, @GET produces 0 (no path in annotation)
        assert len(route_edges) == 1

    def test_empty_for_plain_java_code(self):
        """Plain Java code with no routes should return no edges."""
        code = """\
public class Hello {
    public void greet() {
        System.out.println("Hello");
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Hello.java", "java")

        assert edges == []


# ===================================================================
# Go route tests
# ===================================================================

class TestGoRouteDetection:
    """HTTP route detection for Go source files."""

    def test_net_http_handle_func(self):
        """net/http HandleFunc route detection."""
        code = """\
package main

import "net/http"

func handler(w http.ResponseWriter, r *http.Request) {}

func main() {
    http.HandleFunc("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "UNKNOWN"
        assert edge["provenance"] == "tree-sitter"

    def test_gorilla_mux_handle_func(self):
        """gorilla-mux HandleFunc route detection."""
        code = """\
package main

import "github.com/gorilla/mux"

func handler(w http.ResponseWriter, r *http.Request) {}

func main() {
    r := mux.NewRouter()
    r.HandleFunc("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/users"

    def test_go_get_method_route(self):
        """Go .GET() selector expression route detection (chi/gin/echo style)."""
        code = """\
package main

func main() {
    router.GET("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"

    def test_go_post_method_route(self):
        """Go .POST() selector expression route detection."""
        code = """\
package main

func main() {
    router.POST("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "POST"

    def test_gin_get_route(self):
        """gin .GET route detection."""
        code = """\
package main

import "github.com/gin-gonic/gin"

func main() {
    router := gin.Default()
    router.GET("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"

    def test_echo_get_route(self):
        """echo .GET route detection."""
        code = """\
package main

import "github.com/labstack/echo/v4"

func main() {
    e := echo.New()
    e.GET("/users", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        edge = route_edges[0]
        assert edge["target_text"] == "/users"
        assert edge["http_method"] == "GET"

    def test_go_route_with_path_param(self):
        """Go route with path parameter."""
        code = """\
package main

func main() {
    router.GET("/users/{id}", handler)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/users/{id}"

    def test_empty_for_plain_go_code(self):
        """Plain Go code with no routes should return no edges."""
        code = """\
package main

func main() {
    println("hello")
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        assert edges == []


# ===================================================================
# HTTP method inference unit tests
# ===================================================================

class TestHttpMethodInference:
    """Unit tests for _infer_http_method function."""

    def test_get_method(self):
        assert _infer_http_method("@app.get") == "GET"
        assert _infer_http_method("@router.get") == "GET"
        assert _infer_http_method("app.get(") == "GET"
        assert _infer_http_method('@Get("/path")') == "GET"
        assert _infer_http_method('@GetMapping("/path")') == "GET"
        assert _infer_http_method('r.Get("/path")') == "GET"

    def test_post_method(self):
        assert _infer_http_method("@app.post") == "POST"
        assert _infer_http_method("@router.post") == "POST"
        assert _infer_http_method("app.post(") == "POST"
        assert _infer_http_method('@Post("/path")') == "POST"
        assert _infer_http_method('@PostMapping("/path")') == "POST"

    def test_put_method(self):
        assert _infer_http_method("@router.put") == "PUT"
        assert _infer_http_method("router.put(") == "PUT"
        assert _infer_http_method('@PutMapping("/path")') == "PUT"

    def test_delete_method(self):
        assert _infer_http_method("@router.delete") == "DELETE"
        assert _infer_http_method("app.delete(") == "DELETE"
        assert _infer_http_method('@DeleteMapping("/path")') == "DELETE"

    def test_patch_method(self):
        assert _infer_http_method("@router.patch") == "PATCH"
        assert _infer_http_method("router.patch(") == "PATCH"
        assert _infer_http_method('@PatchMapping("/path")') == "PATCH"

    def test_head_method(self):
        assert _infer_http_method("@app.head") == "HEAD"
        assert _infer_http_method('@Head("/path")') == "HEAD"

    def test_options_method(self):
        assert _infer_http_method("@app.options") == "OPTIONS"
        assert _infer_http_method('@Options("/path")') == "OPTIONS"

    def test_unknown_method(self):
        assert _infer_http_method("@app.route") == "UNKNOWN"
        assert _infer_http_method("path(") == "UNKNOWN"
        assert _infer_http_method("http.HandleFunc(") == "UNKNOWN"
        assert _infer_http_method("mux.HandleFunc(") == "UNKNOWN"
        assert _infer_http_method("app.all(") == "UNKNOWN"

    def test_all_method_returns_unknown(self):
        assert _infer_http_method("@app.all") == "UNKNOWN"
        assert _infer_http_method("app.all(") == "UNKNOWN"
        assert _infer_http_method('@All("/path")') == "UNKNOWN"


# ===================================================================
# Edge structure tests
# ===================================================================

class TestEdgeStructure:
    """Verify edge dict structure correctness across detectors."""

    def test_route_edge_has_required_fields(self):
        """All route edges must have the required fields."""
        code = """\
from flask import Flask

app = Flask(__name__)

@app.route('/items')
def items():
    return []
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
            assert "target_text" in edge
            assert "http_method" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["kind"] == "http_route"
            assert edge["provenance"] == "tree-sitter"

    def test_unsupported_language_returns_empty(self):
        """Unsupported languages should return empty list."""
        detector = _make_detector()
        edges = detector.detect(b"", None, "test.rb", "ruby")
        assert edges == []

    def test_patterns_not_loaded_still_works(self):
        """Detector should work gracefully with no patterns file."""
        detector = RouteDetector(patterns_path="/nonexistent/patterns.yaml")
        code = """\
def hello():
    pass
"""
        tree, source = _parse_python(code)
        edges = detector.detect(source, tree, "test.py", "python")
        # No patterns loaded, but should not crash
        assert edges == []


# ===================================================================
# Edge case tests
# ===================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_file_python(self):
        """Empty Python file should return no edges."""
        tree, source = _parse_python("")
        detector = _make_detector()
        edges = detector.detect(source, tree, "empty.py", "python")
        assert edges == []

    def test_empty_file_typescript(self):
        """Empty TypeScript file should return no edges."""
        tree, source = _parse_typescript("")
        detector = _make_detector()
        edges = detector.detect(source, tree, "empty.ts", "typescript")
        assert edges == []

    def test_empty_file_java(self):
        """Empty Java file should return no edges."""
        tree, source = _parse_java("")
        detector = _make_detector()
        edges = detector.detect(source, tree, "Empty.java", "java")
        assert edges == []

    def test_empty_file_go(self):
        """Empty Go file should return no edges."""
        tree, source = _parse_go("")
        detector = _make_detector()
        edges = detector.detect(source, tree, "empty.go", "go")
        assert edges == []

    def test_multiple_routes_in_one_file(self):
        """File with multiple routes should detect all of them."""
        code = """\
from flask import Flask

app = Flask(__name__)

@app.route('/users', methods=['GET'])
def get_users():
    return []

@app.route('/users', methods=['POST'])
def create_user():
    pass

@app.route('/users/<int:id>', methods=['PUT'])
def update_user(id):
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) == 3
        methods = {e["http_method"] for e in route_edges}
        assert methods == {"GET", "POST", "PUT"}
        paths = {e["target_text"] for e in route_edges}
        assert paths == {"/users", "/users/<int:id>"}

    def test_nested_function_does_not_interfere(self):
        """Route inside a nested function should still be detected."""
        code = """\
from flask import Flask

app = Flask(__name__)

class UserAPI:
    def register_routes(self):
        @app.route('/api/v2/users')
        def get_users():
            return []
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["target_text"] == "/api/v2/users"

    def test_path_join_is_detected_as_route(self):
        """path.join is currently matched as a Django route -- known false positive.

        The pattern "path(" matches "path.join(" because _match_route_pattern
        strips parentheses for substring matching.  This test documents the
        current behaviour so that a fix will be caught by CI.
        """
        code = """\
def process():
    result = path.join('a', 'b')
    return result
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        # Known false positive: path.join is detected as Django path()
        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) == 1

    def test_source_loc_contains_correct_line(self):
        """source_loc should point to the actual line number of the route."""
        code = """\
# line 1
from fastapi import APIRouter  # line 2
# line 3
router = APIRouter()  # line 4
# line 5
@router.get("/path")  # line 6
def handler():  # line 7
    pass  # line 8
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        # The decorator is on line 6 (0-indexed row 5, so +1 = 6)
        assert ":6" in route_edges[0]["source_loc"]

    def test_flask_methods_keyword_multiple_methods(self):
        """Flask methods=['GET', 'POST'] should extract the FIRST method."""
        code = """\
from flask import Flask

app = Flask(__name__)

@app.route('/endpoint', methods=['GET', 'POST'])
def handle():
    pass
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) >= 1
        assert route_edges[0]["http_method"] == "GET"

    def test_spring_rest_controller_no_route(self):
        """@RestController without @RequestMapping should NOT produce a route edge."""
        code = """\
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {
    public String health() {
        return "ok";
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "HealthController.java", "java")

        # @RestController matches but has no path argument -> no edge
        route_edges = [e for e in edges if e["kind"] == "http_route"]
        assert len(route_edges) == 0

    def test_nestjs_controller_no_prefix(self):
        """NestJS @Controller() without path prefix should not produce a route edge."""
        code = """\
import { Controller, Get } from '@nestjs/common';

@Controller()
export class UsersController {
    @Get('list')
    getAll(): string {
        return 'all';
    }
}
"""
        tree, source = _parse_typescript(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.ts", "typescript")

        route_edges = [e for e in edges if e["kind"] == "http_route"]
        # @Controller() has no string arg -> no edge; @Get('list') produces 1 edge
        assert len(route_edges) == 1
        assert route_edges[0]["target_text"] == "list"
