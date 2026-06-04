"""Tests for framework route detection."""

import os
import pytest


class TestFastAPIResolver:
    def test_detect_no_fastapi(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        # Empty directory - no FastAPI
        assert not resolver.detect(str(tmp_path))

    def test_detect_requirements_txt(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        req_path = os.path.join(tmp_path, "requirements.txt")
        with open(req_path, "w") as f:
            f.write("fastapi==0.110.0\nuvicorn\n")

        assert resolver.detect(str(tmp_path))

    def test_detect_pyproject_toml(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        pp_path = os.path.join(tmp_path, "pyproject.toml")
        with open(pp_path, "w") as f:
            f.write('[project]\ndependencies = ["fastapi"]\n')

        assert resolver.detect(str(tmp_path))

    def test_detect_import(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        py_path = os.path.join(tmp_path, "main.py")
        with open(py_path, "w") as f:
            f.write("from fastapi import FastAPI\n\napp = FastAPI()\n")

        assert resolver.detect(str(tmp_path))

    def test_extract_routes_basic(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        py_path = os.path.join(tmp_path, "main.py")
        with open(py_path, "w") as f:
            f.write("""
from fastapi import FastAPI
app = FastAPI()

@app.get("/items")
async def get_items():
    return {"items": []}

@app.post("/items")
def create_item():
    pass
""")

        result = resolver.extract_routes(str(tmp_path), None)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 2

        # Check first route
        node0 = result["nodes"][0]
        assert node0["kind"] == "route"
        assert node0["framework"] == "fastapi"
        assert "GET" in node0["signature"]
        assert "/items" in node0["signature"]

    def test_extract_routes_no_routes(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        py_path = os.path.join(tmp_path, "main.py")
        with open(py_path, "w") as f:
            f.write("""
from fastapi import FastAPI
app = FastAPI()

def regular_function():
    pass
""")

        result = resolver.extract_routes(str(tmp_path), None)
        assert len(result["nodes"]) == 0

    def test_extract_routes_with_router(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        py_path = os.path.join(tmp_path, "router.py")
        with open(py_path, "w") as f:
            f.write("""
from fastapi import APIRouter
router = APIRouter()

@router.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}
""")

        result = resolver.extract_routes(str(tmp_path), None)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["signature"] == "GET /users/{user_id}"

    def test_edge_connects_route_to_handler(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        py_path = os.path.join(tmp_path, "api.py")
        with open(py_path, "w") as f:
            f.write("""
from fastapi import FastAPI
app = FastAPI()

@app.delete("/items/{id}")
def delete_item(id: int):
    pass
""")

        result = resolver.extract_routes(str(tmp_path), None)
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["kind"] == "references"
        assert edge["target_text"].endswith("::delete_item")

    def test_rejects_non_python_files(self, tmp_path):
        from tws_graph.framework.fastapi import FastAPIResolver
        resolver = FastAPIResolver()

        ts_path = os.path.join(tmp_path, "app.ts")
        with open(ts_path, "w") as f:
            f.write("app.get('/items', (req, res) => {})")

        result = resolver.extract_routes(str(tmp_path), None)
        assert len(result["nodes"]) == 0
