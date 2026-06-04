// Sample module for testing the TypeScript extractor.

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
