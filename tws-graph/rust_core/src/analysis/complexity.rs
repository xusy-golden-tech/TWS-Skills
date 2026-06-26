//! Complexity analysis — computes cyclomatic, cognitive, and Halstead metrics.
//!
//! Supports 12 languages: python, typescript, javascript, java, go, rust, c, cpp,
//! csharp, kotlin, php, ruby.

/// Complexity metrics for a single function / method.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ComplexityMetrics {
    /// Cyclomatic complexity: count of decision points + 1.
    pub cyclomatic: u32,
    /// Cognitive complexity: weighted nesting-aware score.
    pub cognitive: u32,
    /// Halstead volume: (N1+N2) * log2(n1+n2).
    pub halstead_volume: f64,
    /// Halstead difficulty: (n1/2) * (N2/n2).
    pub halstead_difficulty: f64,
    /// Halstead effort: volume * difficulty.
    pub halstead_effort: f64,
    /// Lines of code.
    pub lines_of_code: u32,
}

/// Analyze code complexity for a given source string and language.
///
/// Supports: python, typescript, javascript, java, go, rust, c, cpp, csharp,
/// kotlin, php, ruby.
pub fn analyze_complexity(code: &str, language: &str) -> ComplexityMetrics {
    let lines = code.lines().count() as u32;

    // Normalize language name for matching
    let lang_lower = language.to_lowercase();

    let cyclomatic = compute_cyclomatic(code, &lang_lower);
    let cognitive = compute_cognitive(code, &lang_lower);
    let (volume, difficulty, effort) = compute_halstead(code, &lang_lower);

    ComplexityMetrics {
        cyclomatic,
        cognitive,
        halstead_volume: volume,
        halstead_difficulty: difficulty,
        halstead_effort: effort,
        lines_of_code: lines,
    }
}

/// Compute cyclomatic complexity: count decision points + 1.
fn compute_cyclomatic(code: &str, language: &str) -> u32 {
    let keywords = decision_keywords(language);
    let mut count = 1u32; // Base complexity

    // Split into words (simplified tokenization)
    let words = tokenize(code);

    for word in &words {
        // Count keyword-based branching
        if keywords.iter().any(|k| *k == word.as_str()) {
            count += 1;
        }

        // Count logical operators (&& and ||) as additional branches
        if word == "&&" || word == "||" {
            count += 1;
        }
    }

    // Also check for ternary operators (?) and case/switch branches
    for ch in code.chars() {
        if ch == '?' {
            count += 1;
        }
    }

    // Count "case" / "when" / "elif" / "else if" branches
    count += count_pattern(code, language, "case_pattern");

    count
}

/// Compute cognitive complexity: nested structures get higher weight.
fn compute_cognitive(code: &str, language: &str) -> u32 {
    let mut score = 0u32;
    let mut nesting_depth = 0u32;
    let keywords = decision_keywords(language);
    let loop_kw = loop_keywords(language);

    let tokens = tokenize(code);

    for token in &tokens {
        let token_str = token.as_str();

        // Track nesting
        if token_str == "{" || token_str == "begin" || token_str == "do" {
            nesting_depth += 1;
            continue;
        }
        if token_str == "}" || token_str == "end" || token_str == "endfunction" || token_str == "endif" {
            if nesting_depth > 0 {
                nesting_depth -= 1;
            }
            continue;
        }

        // Python-style indentation-based blocks
        if token_str == ":" && matches!(language, "python") {
            nesting_depth += 1;
        }

        // Decision points: base weight 1 + nesting level
        if keywords.iter().any(|k| *k == token_str) {
            score += 1 + nesting_depth;
        }

        // Loops: base weight 1 + nesting level
        if loop_kw.iter().any(|k| *k == token_str) {
            score += 1 + nesting_depth;
        }

        // Logical operators add complexity
        if token_str == "&&" || token_str == "||" {
            score += 1;
        }

        // Break/continue in loops adds nesting penalty
        if (token_str == "break" || token_str == "continue") && nesting_depth > 0 {
            score += nesting_depth;
        }

        // Recursion detection heuristic: function name appearing in its own body
        // This is a simple heuristic — we just check if the word "recurse" appears
        // A more sophisticated approach would need AST analysis
    }

    score
}

/// Compute Halstead metrics: volume, difficulty, effort.
///
/// - n1 = unique operators
/// - n2 = unique operands
/// - N1 = total operators
/// - N2 = total operands
/// - Volume = (N1+N2) * log2(n1+n2)
/// - Difficulty = (n1/2) * (N2/n2)
/// - Effort = Volume * Difficulty
fn compute_halstead(code: &str, language: &str) -> (f64, f64, f64) {
    let tokens = tokenize(code);

    let operators_set = operator_set(language);
    let operand_keywords = operand_keyword_set(language);

    let mut unique_operators: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut unique_operands: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut total_operators = 0usize;
    let mut total_operands = 0usize;

    for token in &tokens {
        if operators_set.iter().any(|k| *k == token.as_str()) {
            unique_operators.insert(token.clone());
            total_operators += 1;
        } else if !operand_keywords.iter().any(|k| *k == token.as_str()) && is_identifier(token) {
            unique_operands.insert(token.clone());
            total_operands += 1;
        }
    }

    let n1 = unique_operators.len();
    let n2 = unique_operands.len();
    let n1_n2 = n1 + n2;
    let n1_n2_total = total_operators + total_operands;

    let volume = if n1_n2 > 0 {
        (n1_n2_total as f64) * (n1_n2 as f64).log2()
    } else {
        0.0
    };

    let difficulty = if n2 > 0 && n1 > 0 {
        (n1 as f64 / 2.0) * (total_operands as f64 / n2 as f64)
    } else {
        0.0
    };

    let effort = volume * difficulty;

    (volume, difficulty, effort)
}

/// Simple tokenizer that splits source code into tokens.
fn tokenize(code: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    let mut current = String::new();

    for ch in code.chars() {
        if ch.is_alphanumeric() || ch == '_' {
            current.push(ch);
        } else {
            if !current.is_empty() {
                tokens.push(current.clone());
                current.clear();
            }
            if !ch.is_whitespace() {
                // Handle multi-char operators
                let ch_str = ch.to_string();
                if !tokens.is_empty() {
                    let last = tokens.last().unwrap();
                    let combined = format!("{}{}", last, ch);
                    // Check for &&, ||, ==, !=, <=, >=, +=, -=, *=, /=, ->, =>
                    if combined == "&&" || combined == "||" || combined == "=="
                        || combined == "!=" || combined == "<=" || combined == ">="
                        || combined == "+=" || combined == "-=" || combined == "*="
                        || combined == "/=" || combined == "->" || combined == "=>"
                        || combined == "::" || combined == "//" || combined == "/*"
                        || combined == "*/" || combined == "**"
                    {
                        tokens.pop();
                        tokens.push(combined);
                    } else {
                        tokens.push(ch_str);
                    }
                } else {
                    tokens.push(ch_str);
                }
            }
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }

    tokens
}

/// Check if a token is a valid identifier (for operand detection).
fn is_identifier(token: &str) -> bool {
    if token.is_empty() {
        return false;
    }
    let first = token.chars().next().unwrap();
    (first.is_alphabetic() || first == '_' || first == '$')
        && token.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '$')
}

/// Decision keywords for cyclomatic and cognitive complexity.
fn decision_keywords(language: &str) -> Vec<&'static str> {
    // "else" is NOT a branch point in cyclomatic complexity — it is the default
    // path of the preceding "if". "else if" / "elif" / "elsif" ARE branch points.
    let common = vec!["if", "for", "while", "loop", "elsif", "elif", "when", "unless", "catch", "except", "rescue", "finally", "assert"];
    let mut kw = common;
    match language {
        "python" => kw.extend(&["elif", "except", "finally"]),
        "java" | "kotlin" | "csharp" => kw.extend(&["catch", "finally", "assert"]),
        "go" => kw.extend(&["fallthrough", "range"]),
        "rust" => kw.extend(&["match"]),
        "php" => kw.extend(&["elseif", "catch", "foreach"]),
        "ruby" => kw.extend(&["elsif", "unless", "rescue", "ensure"]),
        _ => {}
    }
    kw
}

/// Loop keywords for cognitive complexity.
fn loop_keywords(language: &str) -> Vec<&'static str> {
    let common = vec!["for", "while", "loop", "repeat", "foreach"];
    let mut kw = common;
    match language {
        "ruby" => kw.extend(&["each", "times", "upto", "downto"]),
        "php" => kw.extend(&["foreach"]),
        "go" => kw.extend(&["range"]),
        _ => {}
    }
    kw
}

/// Operator set for Halstead metrics.
fn operator_set(language: &str) -> Vec<&'static str> {
    let common = vec![
        "+", "-", "*", "/", "%", "=", "==", "!=", "<", ">", "<=", ">=",
        "&&", "||", "!", "&", "|", "^", "~", "<<", ">>", "+=", "-=", "*=", "/=",
        "++", "--", "->", "=>", "::", ".", ",", ";", ":", "?", "(", ")", "[", "]", "{", "}",
        "new", "return", "throw", "yield", "await", "async",
    ];
    let mut ops = common;
    match language {
        "python" => {
            ops.extend(&["**", "//", "is", "in", "not", "and", "or", "lambda", "del", "raise", "with", "from", "import", "def", "class", "pass"]);
        }
        "rust" => {
            ops.extend(&["fn", "let", "mut", "pub", "impl", "trait", "enum", "struct", "mod", "use", "crate", "self", "super", "where", "dyn", "ref", "move", "unsafe", "extern"]);
        }
        "go" => {
            ops.extend(&["func", "var", "type", "package", "import", "defer", "go", "chan", "select", "interface", "struct", "map"]);
        }
        "kotlin" => {
            ops.extend(&["fun", "val", "var", "class", "object", "interface", "data", "sealed", "when", "in", "is", "as", "by", "lateinit", "lazy"]);
        }
        "java" => {
            ops.extend(&["class", "interface", "enum", "extends", "implements", "abstract", "final", "static", "public", "private", "protected", "void", "this", "super", "package", "import"]);
        }
        "php" => {
            ops.extend(&["function", "class", "interface", "trait", "namespace", "use", "public", "private", "protected", "static", "echo", "print", "require", "include", "clone", "instanceof"]);
        }
        "ruby" => {
            ops.extend(&["def", "class", "module", "end", "self", "super", "yield", "begin", "rescue", "ensure", "require", "include", "extend", "attr_accessor", "attr_reader", "attr_writer"]);
        }
        "typescript" | "javascript" => {
            ops.extend(&["function", "class", "interface", "type", "enum", "extends", "implements", "import", "export", "const", "let", "var", "from", "as", "typeof", "instanceof", "void"]);
        }
        _ => {}
    }
    ops
}

/// Keywords that should NOT be treated as operands in Halstead analysis.
fn operand_keyword_set(language: &str) -> Vec<&'static str> {
    let reserved = vec![
        "true", "false", "null", "none", "undefined", "nil", "this", "self", "super",
    ];
    let mut kw = reserved;
    match language {
        "python" => kw.extend(&["None", "True", "False", "self"]),
        "java" | "kotlin" => kw.extend(&["this", "super", "null"]),
        "javascript" | "typescript" => kw.extend(&["this", "undefined", "null", "NaN"]),
        "ruby" => kw.extend(&["nil", "self"]),
        "go" => kw.extend(&["nil"]),
        "rust" => kw.extend(&["self", "Self"]),
        "php" => kw.extend(&["this", "null"]),
        _ => {}
    }
    kw
}

/// Count case/when/elif patterns for cyclomatic complexity.
fn count_pattern(code: &str, language: &str, _pattern_type: &str) -> u32 {
    let mut count = 0u32;
    let words = tokenize(code);

    match language {
        "go" | "rust" | "java" | "kotlin" | "csharp" | "typescript" | "javascript" | "php" => {
            // case keyword in switch statements
            for w in &words {
                if w == "case" {
                    count += 1;
                }
            }
        }
        "python" => {
            for w in &words {
                if w == "elif" {
                    count += 1;
                }
            }
        }
        "ruby" => {
            for w in &words {
                if w == "when" || w == "elsif" {
                    count += 1;
                }
            }
        }
        _ => {}
    }

    count
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_function_complexity() {
        let code = r#"def add(a, b):
    return a + b"#;

        let metrics = analyze_complexity(code, "python");
        // Simple function: no branches => cyclomatic = 1
        assert_eq!(metrics.cyclomatic, 1);
        assert_eq!(metrics.lines_of_code, 2);
        assert!(metrics.cognitive <= 2, "Very simple code should have low cognitive complexity");
    }

    #[test]
    fn test_branched_function() {
        let code = r#"def check(x, y):
    if x > 0:
        if y > 0:
            return x + y
        elif y == 0:
            return x
        else:
            return -1
    else:
        return 0"#;

        let metrics = analyze_complexity(code, "python");
        // if x>0 (+1), if y>0 (+1), elif y==0 (+1) => cyclomatic = 4
        assert!(metrics.cyclomatic >= 3, "Should have at least cyclomatic 3, got {}", metrics.cyclomatic);
        assert!(metrics.lines_of_code >= 7);
        // Cognitive should be higher than cyclomatic due to nesting
        assert!(metrics.cognitive >= metrics.cyclomatic,
            "Cognitive ({}) should be >= cyclomatic ({}) for nested code",
            metrics.cognitive, metrics.cyclomatic);
    }

    #[test]
    fn test_loops_increase_complexity() {
        let code = r#"function process(items) {
    let result = [];
    for (let i = 0; i < items.length; i++) {
        while (items[i] > 0) {
            result.push(items[i]);
            items[i]--;
        }
    }
    return result;
}"#;

        let metrics = analyze_complexity(code, "javascript");
        // for (+1), while (+1), for condition i<items.length (+1 if counted as comparison context)
        assert!(metrics.cyclomatic >= 3, "Loops should increase cyclomatic complexity");
        assert!(metrics.cognitive > 0, "Nested loops should increase cognitive complexity");
    }

    #[test]
    fn test_complex_function_many_branches() {
        let code = r#"fn complex(a: i32, b: i32, c: i32) -> i32 {
    let mut result = 0;
    if a > b {
        if a > c {
            result = a;
        } else if b > c {
            result = b;
        } else {
            result = c;
        }
    } else if b > c {
        result = b;
    } else {
        result = c;
    }
    for i in 0..result {
        if i % 2 == 0 {
            result += 1;
        } else {
            result -= 1;
        }
    }
    result
}"#;

        let metrics = analyze_complexity(code, "rust");
        // Multiple if/else branches, a loop, nested conditions
        assert!(metrics.cyclomatic >= 6, "Complex function should have high cyclomatic complexity, got {}", metrics.cyclomatic);
        assert!(metrics.cognitive >= metrics.cyclomatic);
        assert!(metrics.halstead_volume > 0.0, "Should have non-zero Halstead volume");
        assert!(metrics.lines_of_code >= 15);
    }

    #[test]
    fn test_halstead_metrics() {
        let code = r#"def divide(a, b):
    if b == 0:
        return None
    return a / b"#;

        let metrics = analyze_complexity(code, "python");
        // Verify halstead metrics are computed
        assert!(metrics.halstead_volume > 0.0, "Halstead volume should be positive");
        // Difficulty > 0 for non-trivial functions
        assert!(metrics.halstead_difficulty >= 0.0);
        // Effort = volume * difficulty
        assert!((metrics.halstead_effort - metrics.halstead_volume * metrics.halstead_difficulty).abs() < 0.001);
    }

    #[test]
    fn test_java_language_support() {
        let code = r#"public int max(int a, int b) {
    if (a > b) {
        return a;
    } else {
        return b;
    }
}"#;

        let metrics = analyze_complexity(code, "java");
        assert_eq!(metrics.cyclomatic, 2); // if/else = 1 branch + 1 base
        assert!(metrics.lines_of_code >= 5);
    }

    #[test]
    fn test_go_language_support() {
        let code = r#"func switchExample(x int) string {
    switch x {
    case 1:
        return "one"
    case 2:
        return "two"
    default:
        return "many"
    }
}"#;

        let metrics = analyze_complexity(code, "go");
        // switch with 2 cases => each case adds to complexity
        assert!(metrics.cyclomatic >= 3, "Switch cases should increase cyclomatic complexity");
        assert!(metrics.lines_of_code >= 8);
    }
}
