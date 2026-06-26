// Sample Rust file for extractor testing — covers real-world patterns:
// use/mod imports, trait impls, derive macros, generics, lifetimes, variables
use std::collections::{HashMap, HashSet};
use std::fmt::{self, Display, Formatter};
use std::io::{Read, Write};
use std::path::PathBuf;

mod database;
pub mod handlers;
mod models;

// --- Trait definitions ---

/// A generic data store trait with a lifetime parameter.
pub trait DataStore<K, V> {
    fn get(&self, key: &K) -> Option<&V>;
    fn set(&mut self, key: K, value: V);
    fn delete(&mut self, key: &K) -> bool;
    fn contains(&self, key: &K) -> bool;
}

/// A cache trait that extends DataStore with TTL support.
pub trait CacheStore<'a, K, V>: DataStore<K, V> {
    fn get_with_ttl(&self, key: &K) -> Option<(&V, u64)>;
    fn flush(&'a mut self);
}

// --- Structs with derive macros ---

/// A record stored in the cache with metadata.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CacheRecord<V> {
    pub key: String,
    pub value: V,
    pub created_at: u64,
    pub ttl_secs: Option<u64>,
}

#[derive(Default)]
pub struct CacheStats {
    pub hits: u64,
    pub misses: u64,
    pub evictions: u64,
}

// --- Trait implementations ---

impl Display for CacheStats {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "CacheStats(hits={}, misses={}, evictions={})",
            self.hits, self.misses, self.evictions
        )
    }
}

/// In-memory implementation of DataStore.
pub struct MemoryStore<K, V> {
    data: HashMap<K, V>,
    stats: CacheStats,
}

impl<K, V> MemoryStore<K, V>
where
    K: Eq + std::hash::Hash,
{
    pub fn new() -> Self {
        MemoryStore {
            data: HashMap::new(),
            stats: CacheStats::default(),
        }
    }

    pub fn stats(&self) -> &CacheStats {
        &self.stats
    }
}

impl<K, V> DataStore<K, V> for MemoryStore<K, V>
where
    K: Eq + std::hash::Hash,
{
    fn get(&self, key: &K) -> Option<&V> {
        let result = self.data.get(key);
        if result.is_some() {
            // stats.hits would be updated here
        }
        result
    }

    fn set(&mut self, key: K, value: V) {
        self.data.insert(key, value);
    }

    fn delete(&mut self, key: &K) -> bool {
        self.data.remove(key).is_some()
    }

    fn contains(&self, key: &K) -> bool {
        self.data.contains_key(key)
    }
}

// --- Functions with generics and lifetimes ---

/// Load configuration from a reader.
pub fn load_config<R: Read>(reader: &mut R) -> std::io::Result<CacheRecord<String>> {
    let mut buffer = String::new();
    reader.read_to_string(&mut buffer)?;

    let record = CacheRecord {
        key: "config".to_string(),
        value: buffer,
        created_at: 0,
        ttl_secs: None,
    };

    Ok(record)
}

/// Save a record using macro calls.
pub fn save_record<W: Write>(record: &CacheRecord<String>, writer: &mut W) -> std::io::Result<()> {
    let json = format!(
        r#"{{"key":"{}", "value":"{}"}}"#,
        record.key, record.value
    );
    writer.write_all(json.as_bytes())?;
    println!("Saved record: {}", record.key);
    Ok(())
}

/// Process an iterator of keys, collecting results into a Vec.
pub fn batch_get<K, V>(
    store: &dyn DataStore<K, V>,
    keys: &[K],
) -> Vec<Option<&V>>
where
    K: Eq + std::hash::Hash,
{
    let mut results = Vec::new();
    for key in keys {
        let val = store.get(key);
        results.push(val);
    }
    results
}
