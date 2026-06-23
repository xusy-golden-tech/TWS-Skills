"""Cross-service detection — route, channel, and gRPC analysis.

P10: Detects HTTP route definitions + HTTP_CALLS edges, channel
publish/subscribe edges, and gRPC service/client/server edges across
supported languages (Python, TypeScript, Java, Go).
"""

from .route_detector import RouteDetector
from .channel_detector import ChannelDetector
from .grpc_detector import GrpcDetector

__all__ = ["RouteDetector", "ChannelDetector", "GrpcDetector"]
