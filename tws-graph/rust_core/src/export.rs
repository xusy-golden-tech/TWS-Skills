//! Graph export — DOT, Mermaid, and JSON serialization.

/// Export a subgraph as DOT (Graphviz) format.
pub fn to_dot(
    _nodes: &[(i64, &str)],
    _edges: &[(i64, i64, &str)],
) -> String {
    // TODO: render DOT digraph
    String::from("digraph G {\n}\n")
}

/// Export a subgraph as Mermaid diagram (Markdown-compatible).
pub fn to_mermaid(
    _nodes: &[(i64, &str)],
    _edges: &[(i64, i64, &str)],
) -> String {
    // TODO: render Mermaid graph
    String::from("graph TD\n")
}

/// Export a subgraph as JSON.
pub fn to_json(
    _nodes: &[(i64, &str)],
    _edges: &[(i64, i64, &str)],
) -> serde_json::Value {
    serde_json::json!({
        "nodes": [],
        "edges": []
    })
}
