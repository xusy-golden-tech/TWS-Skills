//! SQL language extractor.
//!
//! Extracts symbols and relationships from SQL source files (`.sql`)
//! using the tree-sitter-sequel grammar.
//!
//! # Node kinds produced
//! - `sql_table`: `CREATE TABLE` statements
//! - `sql_index`: `CREATE INDEX` statements
//! - `sql_view`: `CREATE VIEW` statements
//! - `sql_query`: `SELECT` / `INSERT` / `UPDATE` / `DELETE` statements
//!
//! # Edge kinds produced
//! - `contains`: containment (file -> table/view/index/query)
//! - `references`: FROM / JOIN / FOREIGN KEY references to tables

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct SqlExtractor;

impl Extractor for SqlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["sql"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["sql"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "sql", &root, HashMap::new());

        walk_sql(source, root, ctx, &file_id)?;

        Ok(())
    }
}

fn walk_sql(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "create_table" => {
                    extract_create_table(source, child, ctx, parent_id)?;
                }
                "create_index" => {
                    extract_create_index(source, child, ctx, parent_id)?;
                }
                "create_view" => {
                    extract_create_view(source, child, ctx, parent_id)?;
                }
                "statement" => {
                    // In sequel grammar, queries are wrapped in statements.
                    // Check children to determine if it's a DML query.
                    extract_statement(source, child, ctx, parent_id)?;
                }
                _ => {
                    walk_sql(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

/// Extract a statement node as a query if it contains SELECT/INSERT/UPDATE/DELETE.
fn extract_statement(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Check if this statement contains a DML operation
    let mut is_dml = false;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let kind = child.kind();
            if kind == "select" || kind == "insert" || kind == "update" || kind == "delete" {
                is_dml = true;
                break;
            }
        }
    }

    if is_dml {
        extract_query(source, node, ctx, parent_id)?;
    } else {
        // May contain create_table/create_view etc., recurse
        walk_sql(source, node, ctx, parent_id)?;
    }

    Ok(())
}

/// Find a direct named child by kind.
fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}

/// Get the identifier text from an object_reference node.
fn get_object_name(source: &[u8], node: Node) -> Option<String> {
    let obj_ref = find_child(node, "object_reference")?;
    let ident = find_child(obj_ref, "identifier")?;
    let text = get_text(source, Some(ident));
    if text.is_empty() { None } else { Some(text) }
}

fn extract_create_table(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // In sequel grammar: create_table → keyword_create, keyword_table, object_reference, column_definitions
    let name = get_object_name(source, node).unwrap_or_else(|| {
        // Fallback: parse from full text
        extract_name_from_create(source, node)
    });
    if name.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;
    let table_id = ctx.add_node(NodeKind::SqlTable, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &table_id, EdgeKind::Contains, line, None);

    // Extract FOREIGN KEY references from full text
    let text = get_text(source, Some(node));
    extract_foreign_key_refs(&text, &table_id, line, ctx);

    Ok(())
}

fn extract_create_index(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // In sequel grammar: create_index → keyword_create, keyword_index, identifier, keyword_on, object_reference, index_fields
    // The index name is a direct identifier (named_child(2))
    let name = if let Some(ident) = find_child(node, "identifier") {
        get_text(source, Some(ident))
    } else {
        get_object_name(source, node).unwrap_or_else(|| {
            extract_name_from_create(source, node)
        })
    };
    if name.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;
    let index_id = ctx.add_node(NodeKind::SqlIndex, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &index_id, EdgeKind::Contains, line, None);

    // Extract table reference from ON clause (text-based)
    let text = get_text(source, Some(node));
    if let Some(table_name) = extract_on_table(&text) {
        let target = hash_id(&ctx.file_path, &table_name);
        ctx.add_edge(&index_id, &target, EdgeKind::References, line, Some(&table_name));
    }

    Ok(())
}

fn extract_create_view(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let name = get_object_name(source, node).unwrap_or_else(|| {
        extract_name_from_create(source, node)
    });
    if name.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;
    let view_id = ctx.add_node(NodeKind::SqlView, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &view_id, EdgeKind::Contains, line, None);

    // Extract FROM / JOIN references from full text
    let text = get_text(source, Some(node));
    for table_name in extract_table_refs(&text) {
        let target = hash_id(&ctx.file_path, &table_name);
        ctx.add_edge(&view_id, &target, EdgeKind::References, line, Some(&table_name));
    }

    // Walk the sub-select for more table refs
    walk_sql(source, node, ctx, &view_id)?;

    Ok(())
}

/// Fallback: parse name from CREATE TABLE/VIEW/INDEX text.
fn extract_name_from_create(source: &[u8], node: Node) -> String {
    let text = get_text(source, Some(node));
    // Extract the identifier between CREATE [TABLE|VIEW|INDEX] and the next keyword/paren
    let rest = text.trim();
    let parts: Vec<&str> = rest.split_whitespace().collect();
    // Parts: ["CREATE", "TABLE", "users", ...] or ["CREATE", "VIEW", "active_users", "AS", ...]
    if parts.len() >= 3 {
        let name = parts[2];
        // Skip if it's a keyword
        if !name.eq_ignore_ascii_case("if")
            && !name.eq_ignore_ascii_case("or")
            && !name.eq_ignore_ascii_case("as")
            && !name.eq_ignore_ascii_case("on")
        {
            // Handle CREATE TABLE IF NOT EXISTS name
            if name.eq_ignore_ascii_case("not") && parts.len() >= 5 {
                return parts[4].to_string();
            }
            return name.to_string();
        }
        // CREATE TABLE IF NOT EXISTS name
        if name.eq_ignore_ascii_case("if") && parts.len() >= 6 {
            return parts[5].to_string();
        }
    }
    String::new()
}

fn extract_query(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let query_text = get_text(source, Some(node));
    let name = if query_text.len() > 60 {
        format!("query:{}...", &query_text[..60].replace('\n', " "))
    } else {
        format!("query:{}", query_text.replace('\n', " "))
    };

    let line = node.start_position().row as u32 + 1;
    let query_id = ctx.add_node(NodeKind::SqlQuery, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &query_id, EdgeKind::Contains, line, None);

    // Extract table references from FROM/JOIN/INTO
    for table_name in extract_table_refs(&query_text) {
        let target = hash_id(&ctx.file_path, &table_name);
        ctx.add_edge(&query_id, &target, EdgeKind::References, line, Some(&table_name));
    }

    // Recurse to find sub-selects for more table refs
    walk_sql(source, node, ctx, &query_id)?;

    Ok(())
}

/// Extract FOREIGN KEY references from table body text.
fn extract_foreign_key_refs(body_text: &str, table_id: &str, line: u32, ctx: &mut ExtractionContext) {
    let upper = body_text.to_uppercase();
    let mut pos = 0;
    while let Some(idx) = upper[pos..].find("REFERENCES") {
        let start = pos + idx + 10; // after "REFERENCES"
        let rest = &body_text[start..].trim();
        let table_name: String = rest
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '"' || *c == '`')
            .collect();
        if !table_name.is_empty() {
            let target = hash_id(&ctx.file_path, &table_name);
            ctx.add_edge(table_id, &target, EdgeKind::References, line, Some(&table_name));
        }
        pos = start;
    }
}

/// Extract table names referenced after FROM, JOIN, INTO, UPDATE keywords.
fn extract_table_refs(text: &str) -> Vec<String> {
    let mut tables = Vec::new();
    let upper = text.to_uppercase();

    let keywords = ["FROM ", "JOIN ", "INTO ", "UPDATE "];
    for kw in &keywords {
        let mut pos = 0;
        while let Some(idx) = upper[pos..].find(kw) {
            let start = pos + idx + kw.len();
            let rest = &text[start..].trim();
            if rest.is_empty() {
                pos = start;
                continue;
            }

            let mut table_name = String::new();
            for c in rest.chars() {
                if c.is_alphanumeric() || c == '_' || c == '"' || c == '.' || c == '`' {
                    table_name.push(c);
                } else if c == '(' {
                    table_name.clear();
                    break;
                } else {
                    break;
                }
            }
            let cleaned = table_name
                .trim_matches('"')
                .trim_matches('`')
                .to_string();
            if !cleaned.is_empty()
                && !cleaned.eq_ignore_ascii_case("select")
                && !cleaned.eq_ignore_ascii_case("where")
                && !cleaned.eq_ignore_ascii_case("on")
                && !cleaned.eq_ignore_ascii_case("set")
                && !cleaned.eq_ignore_ascii_case("values")
                && !cleaned.eq_ignore_ascii_case("exists")
            {
                tables.push(cleaned);
            }
            pos = start;
        }
    }

    tables
}

/// Extract table name from ON clause in CREATE INDEX.
fn extract_on_table(text: &str) -> Option<String> {
    let upper = text.to_uppercase();
    if let Some(idx) = upper.find(" ON ") {
        let rest = &text[idx + 4..].trim();
        let table_name: String = rest
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '"' || *c == '`')
            .collect();
        let cleaned = table_name.trim_matches('"').trim_matches('`').to_string();
        if !cleaned.is_empty() {
            return Some(cleaned);
        }
    }
    None
}

fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::indexer::context::ExtractionContext;
    use crate::traits::{EdgeKind, NodeKind};
    use tree_sitter::Parser;

    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_sequel::LANGUAGE.into())
            .expect("set sql language");
        let tree = parser.parse(source, None).expect("parse sql source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "sql".to_string());
        SqlExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes<'a>(ctx: &'a ExtractionContext, kind: NodeKind) -> Vec<&'a crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges<'a>(ctx: &'a ExtractionContext, kind: EdgeKind) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    #[test]
    fn test_extract_create_table() {
        let ctx = extract("CREATE TABLE users (id INTEGER PRIMARY KEY);", "src/test.sql");
        let tables = find_nodes(&ctx, NodeKind::SqlTable);
        assert_eq!(tables.len(), 1, "Expected 1 sql_table, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        assert_eq!(tables[0].name, "users");
    }

    #[test]
    fn test_extract_create_table_with_fk() {
        let ctx = extract(
            "CREATE TABLE orders (id INTEGER, user_id INTEGER REFERENCES users(id));",
            "src/test.sql",
        );
        let tables = find_nodes(&ctx, NodeKind::SqlTable);
        assert_eq!(tables.len(), 1);

        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("users")), "Expected users FK ref in {:?}", targets);
    }

    #[test]
    fn test_extract_create_index() {
        let ctx = extract("CREATE INDEX idx_users_name ON users(name);", "src/test.sql");
        let indexes = find_nodes(&ctx, NodeKind::SqlIndex);
        assert_eq!(indexes.len(), 1, "Expected 1 sql_index, got {:?}", indexes.iter().map(|i| i.name.as_str()).collect::<Vec<_>>());
        assert_eq!(indexes[0].name, "idx_users_name");

        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("users")), "Expected users table ref in {:?}", targets);
    }

    #[test]
    fn test_extract_create_view() {
        let ctx = extract("CREATE VIEW active_users AS SELECT * FROM users WHERE active = 1;", "src/test.sql");
        let views = find_nodes(&ctx, NodeKind::SqlView);
        assert_eq!(views.len(), 1, "Expected 1 sql_view, got {:?}", views.iter().map(|v| v.name.as_str()).collect::<Vec<_>>());
        assert_eq!(views[0].name, "active_users");
    }

    #[test]
    fn test_extract_select_query() {
        let ctx = extract("SELECT * FROM users JOIN orders ON users.id = orders.user_id;", "src/test.sql");
        let queries = find_nodes(&ctx, NodeKind::SqlQuery);
        assert_eq!(queries.len(), 1, "Expected 1 sql_query, got {:?}", queries.iter().map(|q| &q.name[..q.name.len().min(30)]).collect::<Vec<_>>());

        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("users")), "Expected users in {:?}", targets);
        assert!(targets.iter().any(|t| t.contains("orders")), "Expected orders in {:?}", targets);
    }

    #[test]
    fn test_extract_insert_query() {
        let ctx = extract("INSERT INTO users (name, email) VALUES ('Alice', 'a@b.com');", "src/test.sql");
        let queries = find_nodes(&ctx, NodeKind::SqlQuery);
        assert_eq!(queries.len(), 1, "Expected 1 sql_query");
    }

    #[test]
    fn test_extract_multiple_tables() {
        let ctx = extract(
            "CREATE TABLE users (id INTEGER);\nCREATE TABLE posts (id INTEGER, user_id INTEGER REFERENCES users(id));\n",
            "src/test.sql",
        );
        let tables = find_nodes(&ctx, NodeKind::SqlTable);
        assert_eq!(tables.len(), 2, "Expected 2 tables, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        let names: Vec<&str> = tables.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"users"));
        assert!(names.contains(&"posts"));
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.sql");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_update_query() {
        let ctx = extract("UPDATE users SET name = 'Bob' WHERE id = 1;", "src/test.sql");
        let queries = find_nodes(&ctx, NodeKind::SqlQuery);
        assert_eq!(queries.len(), 1, "Expected 1 sql_query for UPDATE");
    }

    #[test]
    fn test_extract_delete_query() {
        let ctx = extract("DELETE FROM users WHERE id = 1;", "src/test.sql");
        let queries = find_nodes(&ctx, NodeKind::SqlQuery);
        assert_eq!(queries.len(), 1, "Expected 1 sql_query for DELETE");
    }
}
