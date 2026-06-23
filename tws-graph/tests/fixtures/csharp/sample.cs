// Sample module for testing the C# extractor.

using System;
using System.Collections.Generic;
using System.Linq;

namespace SampleApp
{
    public interface IOrderRepository
    {
        List<string> FindById(string id);
        void Save(Dictionary<string, object> order);
    }

    public struct Item
    {
        public string Name;
        public double Price;
    }

    public class OrderService
    {
        private readonly IOrderRepository _repo;
        private static string _apiUrl = "https://api.example.com";

        public OrderService(IOrderRepository repo)
        {
            _repo = repo;
        }

        public Dictionary<string, object> CreateOrder(List<string> items, int userId)
        {
            if (!ValidateInput(userId.ToString()))
            {
                throw new ArgumentException("Invalid user");
            }
            return BuildOrder(items, userId);
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

    public class PaymentHandler
    {
        public bool Process(double amount)
        {
            var service = new OrderService(null);
            service.CreateOrder(new List<string>(), 0);
            double total = CalculateTotal(new List<double> { amount });
            return total > 0;
        }

        private double CalculateTotal(List<double> items)
        {
            return items.Sum();
        }
    }

    public static class Config
    {
        public static string ApiUrl = "https://api.example.com";
        public const int MaxRetries = 3;
    }
}
