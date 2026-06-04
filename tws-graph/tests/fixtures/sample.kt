// Sample module for testing the Kotlin extractor.

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
