"""Cross-service detection — channel and gRPC analysis.

P10: Detects channel publish/subscribe edges, and gRPC
service/client/server edges across supported languages (Python,
TypeScript, Java, Go).  HTTP route detection has moved to the Rust
core (tws-graph routes / trace-request).
"""

from .channel_detector import ChannelDetector
from .grpc_detector import GrpcDetector

__all__ = ["ChannelDetector", "GrpcDetector"]
