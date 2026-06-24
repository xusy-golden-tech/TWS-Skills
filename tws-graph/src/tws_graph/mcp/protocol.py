"""JSON-RPC 2.0 message types for MCP (Model Context Protocol).

Implements a minimal, zero-dependency JSON-RPC 2.0 layer:
- Request: has method, optional params, required id
- Response: has result, id
- Notification: has method, optional params, no id
- Error: has error object, id (optional for parse errors)

Designed for MCP stdio transport: read a line from stdin, parse it,
dispatch to handler, serialize the response, write to stdout.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

# JSON-RPC version constant
JSONRPC = "2.0"


# ============================================================================
# Protocol Exceptions
# ============================================================================


class InvalidRequestError(ValueError):
    """Raised when a message is valid JSON but not a valid JSON-RPC 2.0 message.

    Carries the request id if one was present in the original JSON object,
    so that an error response can be sent back to the client.
    """

    def __init__(self, message: str, id: Optional[int | str] = None) -> None:
        super().__init__(message)
        self.id = id


# ============================================================================
# Error Codes
# ============================================================================


class ErrorCode:
    """JSON-RPC 2.0 standard error codes."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Server-defined error codes (reserved range: -32099 to -32000)
    SERVER_NOT_INITIALIZED = -32002
    SERVER_SHUTTING_DOWN = -32001
    UNKNOWN_ERROR = -32000


# ============================================================================
# Abstract Base Message
# ============================================================================


class JsonRpcMessage(ABC):
    """Abstract base for all JSON-RPC 2.0 messages."""

    jsonrpc: str = JSONRPC

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize this message to a JSON-serializable dict."""
        ...

    def is_notification(self) -> bool:
        """Check whether this message is a notification (no id)."""
        return isinstance(self, JsonRpcNotification)

    def is_error(self) -> bool:
        """Check whether this message is an error response."""
        return isinstance(self, JsonRpcError)


# ============================================================================
# Concrete Message Types
# ============================================================================


@dataclass
class JsonRpcRequest(JsonRpcMessage):
    """JSON-RPC 2.0 request: has method, optional params, required id."""

    method: str
    id: int | str
    params: Optional[dict] = None

    def to_dict(self) -> dict:
        msg: dict = {"jsonrpc": JSONRPC, "method": self.method}
        if self.params is not None:
            msg["params"] = self.params
        msg["id"] = self.id
        return msg


@dataclass
class JsonRpcResponse(JsonRpcMessage):
    """JSON-RPC 2.0 success response: has result and id."""

    result: Any
    id: int | str

    def to_dict(self) -> dict:
        return {"jsonrpc": JSONRPC, "result": self.result, "id": self.id}


@dataclass
class JsonRpcNotification(JsonRpcMessage):
    """JSON-RPC 2.0 notification: has method, optional params, no id."""

    method: str
    params: Optional[dict] = None

    @property
    def id(self) -> None:
        """Notifications have no id per JSON-RPC 2.0 spec."""
        return None

    def to_dict(self) -> dict:
        msg: dict = {"jsonrpc": JSONRPC, "method": self.method}
        if self.params is not None:
            msg["params"] = self.params
        return msg


@dataclass
class JsonRpcError(JsonRpcMessage):
    """JSON-RPC 2.0 error response: has error object and optional id."""

    code: int
    message: str
    id: Optional[int | str] = None
    data: Optional[Any] = None

    @property
    def error(self) -> dict:
        """Return the error object dict."""
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err

    def to_dict(self) -> dict:
        msg: dict = {"jsonrpc": JSONRPC, "error": self.error}
        msg["id"] = self.id
        return msg


# ============================================================================
# Factory Helpers
# ============================================================================


def make_error_response(
    code: int,
    message: str,
    id: Optional[int | str] = None,
    data: Optional[Any] = None,
) -> JsonRpcError:
    """Create a JSON-RPC error response.

    Args:
        code: Numeric error code (use ErrorCode constants).
        message: Human-readable error description.
        id: The id from the request that caused the error (None for parse errors).
        data: Optional additional error data.

    Returns:
        A JsonRpcError instance ready for serialization.
    """
    return JsonRpcError(code=code, message=message, id=id, data=data)


# ============================================================================
# Message Parsing
# ============================================================================


def parse_message(raw: str) -> JsonRpcMessage:
    """Parse a JSON string into a JSON-RPC 2.0 message object.

    Args:
        raw: A complete JSON string representing a single message.

    Returns:
        One of: JsonRpcRequest, JsonRpcResponse, JsonRpcNotification, JsonRpcError.

    Raises:
        InvalidRequestError: If the JSON is valid but the message does not
            conform to JSON-RPC 2.0 and has a recoverable id.
        ValueError: If the JSON is invalid.
    """
    # 1. Parse JSON
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(obj, dict):
        raise ValueError("Invalid JSON-RPC message: expected a JSON object")

    # 2. Validate jsonrpc field
    if obj.get("jsonrpc") != JSONRPC:
        raise ValueError(
            "Invalid JSON-RPC message: jsonrpc field must be '2.0'"
        )

    # 3. Classify and construct
    is_error = "error" in obj
    has_result = "result" in obj
    has_method = "method" in obj
    has_id = "id" in obj

    if is_error:
        err = obj["error"]
        return JsonRpcError(
            code=err.get("code", 0),
            message=err.get("message", "Unknown error"),
            id=obj.get("id"),
            data=err.get("data"),
        )

    if has_result:
        return JsonRpcResponse(result=obj["result"], id=obj.get("id"))

    if has_method and has_id:
        return JsonRpcRequest(
            method=obj["method"],
            params=obj.get("params"),
            id=obj["id"],
        )

    if has_method and not has_id:
        return JsonRpcNotification(
            method=obj["method"],
            params=obj.get("params"),
        )

    raise InvalidRequestError(
        "Invalid JSON-RPC message: missing method/result/error",
        id=obj.get("id"),
    )


def serialize_message(msg: JsonRpcMessage) -> str:
    """Serialize a JSON-RPC 2.0 message to a compact JSON string.

    Args:
        msg: Any JsonRpcMessage instance.

    Returns:
        A JSON string with no trailing whitespace, suitable for line-based
        stdio transport.
    """
    return json.dumps(msg.to_dict(), ensure_ascii=False, separators=(",", ":"))
