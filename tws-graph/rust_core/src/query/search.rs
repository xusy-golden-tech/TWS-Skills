//! FTS5 full-text search with qualifier parsing.
//!
//! Supports qualifiers: `kind:`, `lang:`, `path:`.
//! Example: `tws-graph search kind:function auth lang:python`

/// Parsed search query with optional qualifiers.
#[derive(Debug, Default)]
pub struct SearchQuery {
    pub text: String,
    pub kind: Option<String>,
    pub lang: Option<String>,
    pub path: Option<String>,
}

/// Parse a raw search string into a `SearchQuery`.
///
/// Qualifiers are extracted and stripped from the text portion.
pub fn parse_query(raw: &str) -> SearchQuery {
    let mut query = SearchQuery::default();
    let mut text_parts: Vec<&str> = Vec::new();

    for token in raw.split_whitespace() {
        if let Some(value) = token.strip_prefix("kind:") {
            query.kind = Some(value.to_string());
        } else if let Some(value) = token.strip_prefix("lang:") {
            query.lang = Some(value.to_string());
        } else if let Some(value) = token.strip_prefix("path:") {
            query.path = Some(value.to_string());
        } else {
            text_parts.push(token);
        }
    }

    query.text = text_parts.join(" ");
    query
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_basic() {
        let q = parse_query("auth");
        assert_eq!(q.text, "auth");
        assert!(q.kind.is_none());
    }

    #[test]
    fn test_parse_qualifiers() {
        let q = parse_query("kind:function auth lang:python");
        assert_eq!(q.text, "auth");
        assert_eq!(q.kind.as_deref(), Some("function"));
        assert_eq!(q.lang.as_deref(), Some("python"));
    }

    #[test]
    fn test_parse_path() {
        let q = parse_query("parse path:src/utils");
        assert_eq!(q.text, "parse");
        assert_eq!(q.path.as_deref(), Some("src/utils"));
    }
}
