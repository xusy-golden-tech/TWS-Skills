<?php
// Sample module for testing the PHP extractor.

namespace SampleApp;

use System\Collections\Generic\List as GenericList;
use System\Exception;

require_once 'vendor/autoload.php';
include 'config.php';

interface OrderRepositoryInterface
{
    public function findById(string $id): array;
    public function save(array $order): void;
}

trait LoggerTrait
{
    public function log(string $message): void
    {
        echo "[LOG] " . $message . "\n";
    }
}

class OrderService
{
    private $repo;
    private static $apiUrl = "https://api.example.com";

    public function __construct(OrderRepositoryInterface $repo)
    {
        $this->repo = $repo;
    }

    public function createOrder(array $items, int $userId): array
    {
        if (!self::validateInput((string)$userId)) {
            throw new \InvalidArgumentException("Invalid user");
        }
        return $this->buildOrder($items, $userId);
    }

    private function buildOrder(array $items, int $userId): array
    {
        return [
            'items' => $items,
            'user' => $userId
        ];
    }

    private static function validateInput(string $value): bool
    {
        return strlen($value) > 0;
    }
}

class PaymentHandler
{
    use LoggerTrait;

    public function process(float $amount): bool
    {
        $service = new OrderService(null);
        $service->createOrder([], 0);
        $total = $this->calculateTotal([$amount]);
        return $total > 0;
    }

    private function calculateTotal(array $items): float
    {
        return array_sum($items);
    }
}

function calculate_discount(float $price, float $discount): float
{
    return $price * (1 - $discount);
}

define('MAX_RETRIES', 3);

// =====================================================================
// PHP 8 Attributes (annotations)
// =====================================================================

#[Attribute]
class Route {
    public function __construct(
        public string $path,
        public string $method = 'GET'
    ) {}
}

// Controller using attributes
class UserController {
    #[Route('/users', 'GET')]
    public function index(): array {
        return [];
    }

    #[Route('/users', 'POST')]
    public function store(UserRequest $request): UserResponse {
        return new UserResponse();
    }
}

// =====================================================================
// Trait usage in class body
// =====================================================================

trait CacheableTrait {
    public function getCacheKey(): string {
        return static::class;
    }
}

class CachedOrderService extends OrderService {
    use CacheableTrait;

    private Cache $cache;

    public function __construct(OrderRepositoryInterface $repo, Cache $cache) {
        parent::__construct($repo);
        $this->cache = $cache;
    }
}

// =====================================================================
// Type hints and generic-style docblocks
// =====================================================================

class UserRequest {
    private string $username;
}

class UserResponse {
    private string $status;
}

class Cache {}

class Repository {
    private string $table;

    public function find(string $id): ?array {
        return null;
    }
}
