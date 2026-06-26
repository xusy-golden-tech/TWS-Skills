// Sample Go file for extractor testing — covers real-world patterns:
// imports, interfaces, struct methods, struct tags, variable reads/writes
package sample

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/gorilla/mux"
)

// --- Interface definition ---

// DataStore defines a generic key-value store interface.
type DataStore interface {
	Get(ctx context.Context, key string) (interface{}, error)
	Set(ctx context.Context, key string, value interface{}) error
	Delete(ctx context.Context, key string) error
}

// CacheStore extends DataStore with cache-specific operations.
type CacheStore interface {
	DataStore
	Flush(ctx context.Context) error
	Stats() *CacheStats
}

// CacheStats tracks cache hit/miss counts.
type CacheStats struct {
	Hits   int64
	Misses int64
}

// --- Struct with tags ---

// UserRecord represents a stored user with JSON and validation tags.
type UserRecord struct {
	ID        string    `json:"id" validate:"required,uuid"`
	Name      string    `json:"name" validate:"required,min=1,max=100"`
	Email     string    `json:"email" validate:"required,email"`
	Age       int       `json:"age" validate:"gte=0,lte=150"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at,omitempty"`
}

// --- Struct implementing interface ---

// MemoryStore is an in-memory implementation of DataStore.
type MemoryStore struct {
	data map[string]interface{}
	mu   sync.RWMutex
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		data: make(map[string]interface{}),
	}
}

func (s *MemoryStore) Get(ctx context.Context, key string) (interface{}, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	val, ok := s.data[key]
	if !ok {
		return nil, fmt.Errorf("key %q not found", key)
	}
	return val, nil
}

func (s *MemoryStore) Set(ctx context.Context, key string, value interface{}) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.data[key] = value
	return nil
}

func (s *MemoryStore) Delete(ctx context.Context, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	delete(s.data, key)
	return nil
}

// --- Struct implementing CacheStore (which embeds DataStore) ---

// RedisCache is a Redis-backed cache that implements CacheStore.
type RedisCache struct {
	client *redis.Client
	ttl    time.Duration
}

func NewRedisCache(addr string, ttl time.Duration) *RedisCache {
	return &RedisCache{
		client: redis.NewClient(&redis.Options{Addr: addr}),
		ttl:    ttl,
	}
}

func (c *RedisCache) Get(ctx context.Context, key string) (interface{}, error) {
	return c.client.Get(ctx, key).Result()
}

func (c *RedisCache) Set(ctx context.Context, key string, value interface{}) error {
	data, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("marshal error: %w", err)
	}
	return c.client.Set(ctx, key, data, c.ttl).Err()
}

func (c *RedisCache) Delete(ctx context.Context, key string) error {
	return c.client.Del(ctx, key).Err()
}

func (c *RedisCache) Flush(ctx context.Context) error {
	return c.client.FlushDB(ctx).Err()
}

func (c *RedisCache) Stats() *CacheStats {
	info := c.client.Info(context.Background(), "stats")
	hits := parseInfoField(info, "keyspace_hits")
	misses := parseInfoField(info, "keyspace_misses")
	return &CacheStats{Hits: hits, Misses: misses}
}

func parseInfoField(info, field string) int64 {
	// parsing logic omitted for brevity
	return 0
}

// --- HTTP handler using imports ---

// UserHandler handles HTTP requests for user CRUD.
type UserHandler struct {
	store DataStore
}

func NewUserHandler(store DataStore) *UserHandler {
	return &UserHandler{store: store}
}

func (h *UserHandler) RegisterRoutes(r *mux.Router) {
	r.HandleFunc("/users/{id}", h.GetUser).Methods("GET")
	r.HandleFunc("/users", h.CreateUser).Methods("POST")
	r.HandleFunc("/users/{id}", h.UpdateUser).Methods("PUT")
	r.HandleFunc("/users/{id}", h.DeleteUser).Methods("DELETE")
}

func (h *UserHandler) GetUser(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	user, err := h.store.Get(ctx, id)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(user)
}

func (h *UserHandler) CreateUser(w http.ResponseWriter, r *http.Request) {
	var record UserRecord
	if err := json.NewDecoder(r.Body).Decode(&record); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if err := h.store.Set(ctx, record.ID, record); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
}

func (h *UserHandler) UpdateUser(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	var record UserRecord
	if err := json.NewDecoder(r.Body).Decode(&record); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	record.ID = id

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if err := h.store.Set(ctx, id, record); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusOK)
}

func (h *UserHandler) DeleteUser(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if err := h.store.Delete(ctx, id); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
}

// --- Standalone functions ---

// ComputeStats calculates aggregate statistics.
func ComputeStats(ctx context.Context, store DataStore) (*CacheStats, error) {
	stats := &CacheStats{}
	// iterates store and computes stats
	return stats, nil
}
