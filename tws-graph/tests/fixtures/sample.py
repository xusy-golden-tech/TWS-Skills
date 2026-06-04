"""Sample module for testing the Python extractor."""

import math
from typing import List, Optional


def calculateTotal(items: List[float]) -> float:
    """Calculate the sum of all items."""
    return sum(items)


def _validateInput(value: str) -> bool:
    """Private helper: validate input string."""
    return len(value) > 0


class OrderService:
    """Service class for order management."""

    def createOrder(self, items: List[str], user_id: int) -> dict:
        """Create a new order."""
        if not _validateInput(str(user_id)):
            raise ValueError("Invalid user")
        return self._buildOrder(items, user_id)

    def _buildOrder(self, items: List[str], user_id: int) -> dict:
        """Internal: build order dict."""
        return {"items": items, "user": user_id}


class PaymentHandler:
    """Handles payment processing."""

    def process(self, amount: float) -> bool:
        service = OrderService()
        service.createOrder([], 0)
        total = calculateTotal([amount])
        return total > 0
