// Sample Swift file for extractor testing — covers real-world patterns:
// imports, classes, protocols, structs, enums, extensions,
// inheritance, protocol conformance, visibility, variable reads/writes

import Foundation
import UIKit

// --- Protocol definitions ---

/// DataStore defines a generic key-value store protocol.
public protocol DataStore {
    func get(key: String) -> Any?
    func set(key: String, value: Any)
    func delete(key: String)
}

/// CacheStore extends DataStore with cache-specific operations.
public protocol CacheStore: DataStore {
    func flush()
    func stats() -> CacheStats
}

// --- Struct for statistics ---

public struct CacheStats {
    public var hits: Int64 = 0
    public var misses: Int64 = 0
}

// --- Class with inheritance ---

/// BaseRecord provides common record functionality.
open class BaseRecord {
    public let id: String
    public var createdAt: Date

    public init(id: String) {
        self.id = id
        self.createdAt = Date()
    }
}

// --- Class with properties and methods ---

/// UserRecord represents a stored user.
public class UserRecord: BaseRecord {
    public var name: String
    public var email: String
    private var age: Int
    internal var tags: [String]

    public init(id: String, name: String, email: String, age: Int) {
        self.name = name
        self.email = email
        self.age = age
        self.tags = []
        super.init(id: id)
    }

    public func displayName() -> String {
        return "\(name) <\(email)>"
    }

    private func validateAge() -> Bool {
        return age > 0 && age < 150
    }
}

// --- Struct implementing protocol ---

/// MemoryStore is an in-memory implementation of DataStore.
public struct MemoryStore: DataStore {
    private var storage: [String: Any] = [:]

    public func get(key: String) -> Any? {
        return storage[key]
    }

    public func set(key: String, value: Any) {
        storage[key] = value
    }

    public func delete(key: String) {
        storage.removeValue(forKey: key)
    }
}

// --- Enum ---

public enum Status {
    case active
    case inactive
    case pending
}

// --- Extension ---

extension UserRecord: CustomStringConvertible {
    public var description: String {
        return "UserRecord(id: \(id), name: \(name))"
    }
}

// --- Standalone function ---

public func computeStats(store: DataStore) -> CacheStats {
    var stats = CacheStats()
    stats.hits = 42
    stats.misses = 7
    return stats
}
