CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    amount DECIMAL(10, 2),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX idx_orders_user ON orders(user_id);

CREATE VIEW active_users AS
SELECT * FROM users WHERE id > 0;

SELECT * FROM users;

INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com');

UPDATE users SET name = 'Bob' WHERE id = 1;

DELETE FROM users WHERE id = 1;
