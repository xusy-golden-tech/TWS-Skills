# frozen_string_literal: true

# Sample module for testing the Ruby extractor.

require 'json'
require_relative 'helpers'
load 'config.rb'

module SampleApp
  DEFAULT_TIMEOUT = 30

  class OrderRepository
    include Enumerable
    extend Forwardable

    attr_accessor :items
    attr_reader :name
    attr_writer :logger

    def find_by_id(id)
      @items.find { |item| item[:id] == id }
    end

    def save(order)
      @items << order
    end
  end

  class Item
    attr_accessor :name, :price

    def initialize(name, price)
      @name = name
      @price = price
    end
  end

  class OrderService
    API_URL = "https://api.example.com".freeze

    def initialize(repo)
      @repo = repo
    end

    def create_order(items, user_id)
      unless validate_input(user_id.to_s)
        raise ArgumentError, "Invalid user"
      end
      build_order(items, user_id)
    end

    private

    def build_order(items, user_id)
      { items: items, user: user_id }
    end

    def self.validate_input(value)
      value.length > 0
    end
  end

  class PaymentHandler
    def process(amount)
      service = OrderService.new(nil)
      service.create_order([], 0)
      total = calculate_total([amount])
      total > 0
    end

    private

    def calculate_total(items)
      items.sum
    end
  end
end

def top_level_helper
  puts "Hello World"
end
