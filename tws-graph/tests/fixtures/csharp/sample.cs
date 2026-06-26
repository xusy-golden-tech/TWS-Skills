// Sample C# module for extractor testing — covers:
// using imports, attributes/decorates, generics/type_ref, LINQ, property accessors
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

namespace SampleApp
{
    // --- Interface ---
    public interface IOrderRepository
    {
        Task<List<OrderDto>> FindByIdAsync(string id);
        Task SaveAsync(OrderDto order);
    }

    // --- DTO with attributes ---
    public class OrderDto
    {
        [JsonPropertyName("order_id")]
        public string Id { get; set; }

        [JsonPropertyName("customer_name")]
        public string CustomerName { get; set; }

        [JsonPropertyName("total_amount")]
        public decimal TotalAmount { get; set; }

        [JsonPropertyName("items")]
        public List<OrderItem> Items { get; set; }

        [JsonPropertyName("created_at")]
        public DateTime CreatedAt { get; set; }
    }

    public class OrderItem
    {
        [JsonPropertyName("product_id")]
        public string ProductId { get; set; }

        [JsonPropertyName("quantity")]
        public int Quantity { get; set; }

        [JsonPropertyName("unit_price")]
        public decimal UnitPrice { get; set; }
    }

    // --- Struct ---
    public struct Item
    {
        public string Name { get; set; }
        public double Price { get; set; }
    }

    // --- Generic repository ---
    public class GenericRepository<T> where T : class
    {
        private readonly List<T> _items = new List<T>();

        public void Add(T item)
        {
            _items.Add(item);
        }

        public T GetById(string id)
        {
            return _items.FirstOrDefault();
        }

        public List<T> Filter(Func<T, bool> predicate)
        {
            return _items.Where(predicate).ToList();
        }
    }

    // --- Service with LINQ ---
    public class OrderService
    {
        private readonly IOrderRepository _repo;
        private static string _apiUrl = "https://api.example.com";

        public OrderService(IOrderRepository repo)
        {
            _repo = repo;
        }

        public List<OrderDto> GetHighValueOrders(List<OrderDto> orders, decimal threshold)
        {
            // LINQ chain: Where → OrderByDescending → Select → ToList
            return orders
                .Where(o => o.TotalAmount > threshold)
                .OrderByDescending(o => o.TotalAmount)
                .Select(o => new OrderDto
                {
                    Id = o.Id,
                    CustomerName = o.CustomerName.ToUpper(),
                    TotalAmount = o.TotalAmount,
                    Items = o.Items
                        .Where(i => i.Quantity > 0)
                        .OrderBy(i => i.UnitPrice)
                        .ToList()
                })
                .ToList();
        }

        public Dictionary<string, decimal> GetTotalsByCustomer(List<OrderDto> orders)
        {
            return orders
                .GroupBy(o => o.CustomerName)
                .ToDictionary(
                    g => g.Key,
                    g => g.Sum(o => o.TotalAmount)
                );
        }

        public bool HasOrdersForCustomer(string customerName)
        {
            var allOrders = new List<OrderDto>();
            return allOrders.Any(o => o.CustomerName == customerName);
        }

        private Dictionary<string, object> BuildOrder(List<string> items, int userId)
        {
            return new Dictionary<string, object>
            {
                { "items", items },
                { "user", userId }
            };
        }

        private static bool ValidateInput(string value)
        {
            return value.Length > 0;
        }
    }

    // --- Payment handler ---
    public class PaymentHandler
    {
        public bool Process(decimal amount)
        {
            var service = new OrderService(null);
            double total = CalculateTotal(new List<double> { (double)amount });
            return total > 0;
        }

        private double CalculateTotal(List<double> items)
        {
            return items.Sum();
        }
    }

    // --- Static config ---
    public static class Config
    {
        public static string ApiUrl = "https://api.example.com";
        public const int MaxRetries = 3;
    }
}
