//! Python language extractor.
//!
//! Extracts symbols and relationships from Python source files (`.py`, `.pyi`)
//! using the tree-sitter-python grammar.
//!
//! # Node kinds produced
//! - `class`: class definition
//! - `function`: module-level function
//! - `method`: function inside a class
//! - `module`: imported module name
//! - `variable`: module-level assignment (ALL_CAPS constants, `__all__`)
//!
//! # Edge kinds produced
//! - `calls`: function/method calls (including `self.method`, `module.func`)
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import statements (including relative imports resolved to absolute paths)
//! - `references`: per-symbol import references from `from X import Y` (target_text = "X.Y")
//! - `extends`: class inheritance
//! - `implements`: metaclass=ABCMeta detection
//! - `decorates`: decorator application
//! - `type_ref`: type annotations in parameters and return types
//! - `env_accesses`: `os.environ[...]` / `os.getenv(...)` access

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;
use tree_sitter::TreeCursor;

// ---------------------------------------------------------------------------
// Python built-in names -- filtered from type annotations and edges
// ---------------------------------------------------------------------------

const PYTHON_BUILTINS: &[&str] = &[
    "str", "int", "float", "bool", "bytes", "list", "dict", "tuple", "set",
    "frozenset", "None", "True", "False", "object", "type", "range", "slice",
    "complex", "memoryview", "bytearray", "property", "staticmethod",
    "classmethod", "super", "self", "cls", "NotImplemented", "Ellipsis",
    "any", "all", "print", "len", "isinstance", "issubclass", "hasattr",
    "getattr", "setattr", "delattr", "iter", "next", "open", "input",
    "repr", "format", "map", "filter", "zip", "enumerate", "sorted",
    "reversed", "abs", "min", "max", "sum", "round", "pow", "divmod",
    "chr", "ord", "hex", "oct", "bin", "id", "callable", "compile", "eval",
    "exec", "globals", "locals", "vars", "dir", "help", "__import__",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "RuntimeError", "OSError", "IOError", "ImportError", "AttributeError",
    "StopIteration", "NotImplementedError",
];

fn is_python_builtin(name: &str) -> bool {
    PYTHON_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// PythonExtractor
// ---------------------------------------------------------------------------

pub struct PythonExtractor;

impl Extractor for PythonExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["py", "pyi"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["python"]
    }

    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();

        // Clone the file name to avoid borrow conflict with ctx
        let file_name = {
            let path = std::path::Path::new(&ctx.file_path);
            path.file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("module")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        // Process all top-level statements in the module body
        let mut walker = Walker::new();
        walker.walk_body(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker -- scope-aware tree walker (stores no borrows, passes ctx around)
// ---------------------------------------------------------------------------

struct Walker {
    /// Stack of class names for building self.method targets.
    class_stack: Vec<String>,
    /// Map from imported name (including aliases) to qualified module.symbol name.
    /// E.g. `from os.path import join` populates `"join" → "os.path.join"`.
    /// `from os.path import join as jn` populates `"jn" → "os.path.join"`.
    imported_names: HashMap<String, String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            class_stack: Vec::new(),
            imported_names: HashMap::new(),
        }
    }

    fn current_class(&self) -> Option<&str> {
        self.class_stack.last().map(|s| s.as_str())
    }

    // ------------------------------------------------------------------
    // Body walking
    // ------------------------------------------------------------------

    fn walk_body(
        &mut self,
        source: &[u8],
        body: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let mut cursor: TreeCursor = body.walk();
        if cursor.goto_first_child() {
            loop {
                let node = cursor.node();
                if node.is_named() {
                    match node.kind() {
                        "class_definition" => {
                            self.extract_class(source, node, ctx, parent_id)?;
                        }
                        "function_definition" => {
                            self.extract_function(source, node, ctx, parent_id, NodeKind::Function)?;
                        }
                        "decorated_definition" => {
                            self.extract_decorated(source, node, ctx, parent_id)?;
                        }
                        "import_statement" => {
                            self.extract_import_stmt(source, node, ctx, parent_id)?;
                        }
                        "import_from_statement" => {
                            self.extract_import_from(source, node, ctx, parent_id)?;
                        }
                        "expression_statement" => {
                            self.walk_expression_stmt(source, node, ctx, parent_id)?;
                        }
                        "assignment" => {
                            self.extract_module_assignment(source, node, ctx, parent_id)?;
                        }
                        _ => {}
                    }
                }
                if !cursor.goto_next_sibling() {
                    break;
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    fn extract_class(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }
        self.extract_class_impl(source, node, ctx, parent_id, &[])
    }

    fn extract_class_impl(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        if let Some(body) = node.child_by_field_name("body") {
            if let Some(doc) = extract_docstring(source, body) {
                extra.insert("docstring".to_string(), doc);
            }
        }

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        if let Some(superclasses) = node.child_by_field_name("superclasses") {
            for i in 0..superclasses.named_child_count() {
                if let Some(sc) = superclasses.named_child(i) {
                    let super_name = resolve_attribute_chain(source, sc);
                    if !super_name.is_empty() && !is_python_builtin(&super_name) {
                        let target_qn = format!("{}::{}", ctx.file_path, super_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&super_name));
                    }
                }
            }
            self.check_metaclass(source, superclasses, &class_id, ctx, node);
        }

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_class_body_statement(source, child, ctx, &class_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(class_id)
    }

    fn walk_class_body_statement(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "function_definition" => {
                self.extract_function(source, node, ctx, parent_id, NodeKind::Method)?;
            }
            "decorated_definition" => {
                self.extract_decorated(source, node, ctx, parent_id)?;
            }
            "expression_statement" => {
                self.walk_expression_stmt(source, node, ctx, parent_id)?;
            }
            "assignment" => {
                self.extract_class_assignment(source, node, ctx, parent_id)?;
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_class_body_statement(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        fn_kind: NodeKind,
    ) -> anyhow::Result<String> {
        self.extract_function_with_decorators(source, node, ctx, parent_id, fn_kind, &[])
    }

    fn extract_function_with_decorators(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        fn_kind: NodeKind,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        // Signature
        if let Some(params) = node.child_by_field_name("parameters") {
            let sig = get_text(source, Some(params));
            if !sig.is_empty() {
                extra.insert("signature".to_string(), format!("def {}({})", name, sig));
            }
        }

        // Docstring
        if let Some(body) = node.child_by_field_name("body") {
            if let Some(doc) = extract_docstring(source, body) {
                extra.insert("docstring".to_string(), doc);
            }
        }

        let func_id = ctx.add_node(fn_kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        // Type annotations on parameters
        if let Some(params) = node.child_by_field_name("parameters") {
            self.extract_param_types(source, params, &func_id, node, ctx);
        }

        // Return type annotation
        if let Some(ret_type) = node.child_by_field_name("return_type") {
            let type_text = get_text(source, Some(ret_type));
            if !type_text.is_empty() && !is_python_builtin(&type_text) {
                let target_qn = format!("{}::{}", ctx.file_path, type_text);
                let target = hash_id(&ctx.file_path, &target_qn);
                let qualified_type = qualify_type_text(&self.imported_names, &type_text);
                ctx.add_edge(&func_id, &target, EdgeKind::TypeRef, line, Some(&qualified_type));
            }
        }

        // Push scope and process body for calls
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &func_id)?;
        }

        ctx.pop_scope();
        Ok(func_id)
    }

    /// Walk body recursively to find `call` nodes.
    fn walk_body_for_calls(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "call" => {
                self.extract_call(source, node, ctx, parent_id)?;
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() == "call" {
                            self.extract_call(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
            // Nested class/function definitions inside function bodies
            "class_definition" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "function_definition" => {
                self.extract_function(source, node, ctx, parent_id, NodeKind::Function)?;
            }
            "decorated_definition" => {
                self.extract_decorated(source, node, ctx, parent_id)?;
            }
            "subscript" => {
                // Check for os.environ[...]
                let line = node.start_position().row as u32 + 1;
                self.check_env_subscript(source, node, ctx, parent_id, line);
                // Also recurse into children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "block"
            | "expression_statement"
            | "return_statement"
            | "assignment"
            | "if_statement"
            | "for_statement"
            | "while_statement"
            | "with_statement"
            | "try_statement"
            | "elif_clause"
            | "else_clause"
            | "except_clause"
            | "except_group_clause"
            | "finally_clause"
            | "match_statement"
            | "case_clause"
            | "list"
            | "tuple"
            | "dictionary"
            | "set"
            | "list_comprehension"
            | "dictionary_comprehension"
            | "set_comprehension"
            | "generator_expression"
            | "parenthesized_expression"
            | "binary_operator"
            | "boolean_operator"
            | "comparison_operator"
            | "not_operator"
            | "unary_operator"
            | "lambda"
            | "conditional_expression"
            | "named_expression"
            | "await"
            | "yield"
            | "assert_statement"
            | "delete_statement"
            | "raise_statement"
            | "global_statement"
            | "nonlocal_statement"
            | "pass_statement"
            | "break_statement"
            | "continue_statement"
            | "concatenated_string"
            | "string"
            | "attribute"
            | "keyword_argument"
            | "pair" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            // Imports inside function bodies: extract them so they populate
            // `imported_names` for later calls in the same function.
            "import_statement" => {
                self.extract_import_stmt(source, node, ctx, parent_id)?;
            }
            "import_from_statement" => {
                self.extract_import_from(source, node, ctx, parent_id)?;
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call"
                            || ck == "attribute"
                            || ck == "subscript"
                            || ck == "return_statement"
                            || ck == "class_definition"
                            || ck == "function_definition"
                            || ck == "decorated_definition"
                        {
                            self.walk_body_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let func = node.child_by_field_name("function");
        let line = node.start_position().row as u32 + 1;

        match func {
            Some(f) => match f.kind() {
                "identifier" => {
                    let name = get_text(source, Some(f));
                    if !name.is_empty() && !is_python_builtin(&name) {
                        let target_qn = build_call_target(
                            &ctx.file_path,
                            &self.class_stack,
                            &name,
                            false,
                        );
                        let target = hash_id(&ctx.file_path, &target_qn);
                        // If name was imported, use qualified module.symbol as target_text
                        let call_target_text = self
                            .imported_names
                            .get(&name)
                            .map(|s| s.as_str())
                            .unwrap_or(&name);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(call_target_text));
                    }
                }
                "attribute" => {
                    self.extract_attribute_call(source, f, ctx, parent_id, line)?;
                }
                "subscript" => {
                    self.check_env_subscript(source, f, ctx, parent_id, line);
                }
                "call" => {
                    self.extract_call(source, f, ctx, parent_id)?;
                }
                _ => {}
            },
            None => {}
        }

        Ok(())
    }

    fn extract_attribute_call(
        &self,
        source: &[u8],
        attr_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
    ) -> anyhow::Result<()> {
        let full_chain = resolve_attribute_chain(source, attr_node);
        if full_chain.is_empty() {
            return Ok(());
        }

        // os.getenv / os.environ.get
        if full_chain == "os.getenv" || full_chain == "os.environ.get" {
            ctx.add_edge(parent_id, parent_id, EdgeKind::EnvAccesses, line, Some("os.getenv"));
            return Ok(());
        }

        if let Some(rest) = full_chain.strip_prefix("self.") {
            let target_qn = build_call_target(&ctx.file_path, &self.class_stack, rest, true);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full_chain));
        } else if let Some(rest) = full_chain.strip_prefix("cls.") {
            let target_qn = build_call_target(&ctx.file_path, &self.class_stack, rest, true);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full_chain));
        } else if !is_python_builtin(&full_chain) {
            let callee = full_chain.rsplitn(2, '.').next().unwrap_or(&full_chain);
            let target_qn = format!("{}::{}", ctx.file_path, callee);
            let target = hash_id(&ctx.file_path, &target_qn);
            // If the first segment is an imported module, qualify the target_text
            let qualified_chain = qualify_chain(&self.imported_names, &full_chain);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified_chain));
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Decorated definitions
    // ------------------------------------------------------------------

    fn extract_decorated(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let mut decorator_names: Vec<String> = Vec::new();

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "decorator" {
                    if let Some(expr) = child.named_child(0) {
                        let dec_name = resolve_attribute_chain(source, expr);
                        if !dec_name.is_empty() {
                            decorator_names.push(dec_name);
                        }
                    }
                }
            }
        }

        // Extract the inner definition (class or function), then add decorates edges
        if let Some(def) = node.child_by_field_name("definition") {
            let def_kind = def.kind();
            let is_inside_class = self.current_class().is_some();
            let fn_kind = if is_inside_class {
                NodeKind::Method
            } else {
                NodeKind::Function
            };

            let line = node.start_position().row as u32 + 1;

            if def_kind == "class_definition" {
                let class_id =
                    self.extract_class_impl(source, def, ctx, parent_id, &decorator_names)?;
                for dec_name in &decorator_names {
                    if !is_python_builtin(dec_name) {
                        let target_qn = format!("{}::{}", ctx.file_path, dec_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&class_id, &target, EdgeKind::Decorates, line, Some(dec_name));
                    }
                }
            } else if def_kind == "function_definition" {
                let func_id = self.extract_function_with_decorators(
                    source,
                    def,
                    ctx,
                    parent_id,
                    fn_kind,
                    &decorator_names,
                )?;
                for dec_name in &decorator_names {
                    if !is_python_builtin(dec_name) {
                        let target_qn = format!("{}::{}", ctx.file_path, dec_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            &func_id,
                            &target,
                            EdgeKind::Decorates,
                            line,
                            Some(dec_name),
                        );
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    fn extract_import_stmt(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "dotted_name" => {
                        let module_name = get_text(source, Some(child));
                        if !module_name.is_empty() {
                            let target_qn = format!("{}::{}", ctx.file_path, module_name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&module_name));
                            // Populate imported_names so we can qualify references
                            // e.g. `import foo.bar` → imported names: "foo.bar" → "foo.bar" (top-level)
                            let top = module_name.split('.').next().unwrap_or(&module_name);
                            self.imported_names.insert(top.to_string(), module_name.clone());
                        }
                    }
                    "aliased_import" => {
                        // import foo.bar as fb → IMPORTS edge + map alias
                        if let Some(name_node) = child.child_by_field_name("name") {
                            let module_name = get_text(source, Some(name_node));
                            if !module_name.is_empty() {
                                let target_qn = format!("{}::{}", ctx.file_path, module_name);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&module_name));
                                if let Some(alias_node) = child.child_by_field_name("alias") {
                                    let alias = get_text(source, Some(alias_node));
                                    if !alias.is_empty() && !is_python_builtin(&alias) {
                                        self.imported_names.insert(alias.to_string(), module_name.clone());
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    fn extract_import_from(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;
        let module_node = node.child_by_field_name("module_name");
        let module_id = module_node.map(|m| m.id());
        let raw_module_name = get_text(source, module_node);

        // Resolve relative imports (e.g. ".foo", "..sibling") to absolute module paths
        let module_name = if raw_module_name.starts_with('.') {
            resolve_relative_import(&raw_module_name, &ctx.file_path)
        } else {
            raw_module_name
        };

        if !module_name.is_empty() {
            // 1. Module-level IMPORTS edge
            let target_qn = format!("{}::{}", ctx.file_path, module_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&module_name));

            // 2. Per-symbol REFERENCES edges + populate imported_names map
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    // Skip the module_name child itself
                    if module_id == Some(child.id()) {
                        continue;
                    }
                    match child.kind() {
                        "dotted_name" => {
                            // from X import Y → REFERENCES edge target_text = "X.Y"
                            let symbol_name = get_text(source, Some(child));
                            if !symbol_name.is_empty() && !is_python_builtin(&symbol_name) {
                                let ref_target = format!("{}.{}", module_name, symbol_name);
                                let ref_target_qn =
                                    format!("{}::{}", ctx.file_path, ref_target);
                                let ref_id = hash_id(&ctx.file_path, &ref_target_qn);
                                ctx.add_edge(
                                    parent_id,
                                    &ref_id,
                                    EdgeKind::References,
                                    line,
                                    Some(&ref_target),
                                );
                                self.imported_names
                                    .insert(symbol_name.to_string(), ref_target);
                            }
                        }
                        "aliased_import" => {
                            // from X import Y as Z → REFERENCES target_text = "X.Y"
                            if let Some(name_node) = child.child_by_field_name("name") {
                                let original_name = get_text(source, Some(name_node));
                                if !original_name.is_empty() && !is_python_builtin(&original_name) {
                                    let ref_target =
                                        format!("{}.{}", module_name, original_name);
                                    let ref_target_qn =
                                        format!("{}::{}", ctx.file_path, ref_target);
                                    let ref_id = hash_id(&ctx.file_path, &ref_target_qn);
                                    ctx.add_edge(
                                        parent_id,
                                        &ref_id,
                                        EdgeKind::References,
                                        line,
                                        Some(&ref_target),
                                    );
                                    // Map the alias (or original name if no alias) → qualified name
                                    if let Some(alias_node) =
                                        child.child_by_field_name("alias")
                                    {
                                        let alias = get_text(source, Some(alias_node));
                                        if !alias.is_empty() {
                                            self.imported_names
                                                .insert(alias.to_string(), ref_target);
                                        }
                                    } else {
                                        self.imported_names
                                            .insert(original_name.to_string(), ref_target);
                                    }
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Assignment extraction
    // ------------------------------------------------------------------

    fn extract_module_assignment(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // Only if not inside a class
        if self.current_class().is_some() {
            return Ok(());
        }
        self.extract_assignment_impl(source, node, ctx, parent_id)
    }

    fn extract_class_assignment(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        if let Some(left) = node.child_by_field_name("left") {
            let left_text = get_text(source, Some(left));
            if !left_text.is_empty() && is_all_caps(&left_text) {
                let var_id = ctx.add_node(NodeKind::Variable, &left_text, &node, HashMap::new());
                let line = node.start_position().row as u32 + 1;
                ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
            }
        }
        Ok(())
    }

    fn extract_assignment_impl(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        if let Some(left) = node.child_by_field_name("left") {
            let left_text = get_text(source, Some(left));

            if left_text == "__all__" {
                let var_id = ctx.add_node(NodeKind::Variable, &left_text, &node, HashMap::new());
                let line = node.start_position().row as u32 + 1;
                ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
                return Ok(());
            }

            if is_all_caps(&left_text) {
                let var_id = ctx.add_node(NodeKind::Constant, &left_text, &node, HashMap::new());
                let line = node.start_position().row as u32 + 1;
                ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Expression statement
    // ------------------------------------------------------------------

    fn walk_expression_stmt(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "call" => {
                        self.extract_call(source, child, ctx, parent_id)?;
                    }
                    "assignment" => {
                        self.extract_module_assignment(source, child, ctx, parent_id)?;
                    }
                    "attribute" | "subscript" => {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Type annotations (on parameters)
    // ------------------------------------------------------------------

    fn extract_param_types(
        &self,
        source: &[u8],
        params_node: Node,
        func_id: &str,
        func_node: Node,
        ctx: &mut ExtractionContext,
    ) {
        let line = func_node.start_position().row as u32 + 1;
        for i in 0..params_node.named_child_count() {
            if let Some(param) = params_node.named_child(i) {
                match param.kind() {
                    "typed_parameter" | "typed_default_parameter" => {
                        if let Some(type_node) = param.child_by_field_name("type") {
                            let type_text = get_text(source, Some(type_node));
                            if !type_text.is_empty() && !is_python_builtin(&type_text) {
                                let target_qn = format!("{}::{}", ctx.file_path, type_text);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                let qualified_type =
                                    qualify_type_text(&self.imported_names, &type_text);
                                ctx.add_edge(
                                    func_id,
                                    &target,
                                    EdgeKind::TypeRef,
                                    line,
                                    Some(&qualified_type),
                                );
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Env access check
    // ------------------------------------------------------------------

    fn check_env_subscript(
        &self,
        source: &[u8],
        subscript_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
    ) {
        if let Some(value) = subscript_node.child_by_field_name("value") {
            let attr_name = resolve_attribute_chain(source, value);
            if attr_name == "os.environ" {
                ctx.add_edge(
                    parent_id,
                    parent_id,
                    EdgeKind::EnvAccesses,
                    line,
                    Some("os.environ"),
                );
            }
        }
    }

    // ------------------------------------------------------------------
    // metaclass=ABCMeta check
    // ------------------------------------------------------------------

    fn check_metaclass(
        &self,
        source: &[u8],
        superclasses: Node,
        class_id: &str,
        ctx: &mut ExtractionContext,
        class_node: Node,
    ) {
        for i in 0..superclasses.named_child_count() {
            if let Some(child) = superclasses.named_child(i) {
                if child.kind() == "keyword_argument" {
                    if let Some(name_node) = child.child_by_field_name("name") {
                        if get_text(source, Some(name_node)) == "metaclass" {
                            if let Some(value) = child.child_by_field_name("value") {
                                let val = resolve_attribute_chain(source, value);
                                if val.contains("ABCMeta") {
                                    let line = class_node.start_position().row as u32 + 1;
                                    let target_qn = format!("{}::ABCMeta", ctx.file_path);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(
                                        class_id,
                                        &target,
                                        EdgeKind::Implements,
                                        line,
                                        Some("ABCMeta"),
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Resolve a Python relative import module name to its absolute dotted module path.
///
/// Python relative imports use leading dots to indicate the current and parent
/// packages. For example, given a file at `src/pkg/subpkg/module.py`:
/// - `.foo` → `pkg.subpkg.foo`
/// - `..sibling` → `pkg.sibling`
/// - `...grandparent` → `grandparent`
///
/// Non-relative names (no leading dot) are returned unchanged.
fn resolve_relative_import(relative_name: &str, file_path: &str) -> String {
    if !relative_name.starts_with('.') {
        return relative_name.to_string();
    }

    let dots = relative_name.chars().take_while(|c| *c == '.').count();
    let rest = &relative_name[dots..]; // remove leading dots

    // Get directory of current file
    let dir = std::path::Path::new(file_path)
        .parent()
        .unwrap_or(std::path::Path::new(""));
    let dir_str = dir.to_str().unwrap_or("");

    // Split into segments (both forward and backslashes)
    let mut segments: Vec<&str> = dir_str
        .split(&['/', '\\'][..])
        .filter(|s| !s.is_empty())
        .collect();

    // N dots means go up N-1 levels:
    // .foo   → 1 dot → pop 0 → stay in current dir, append foo
    // ..foo  → 2 dots → pop 1 → go up one dir, append foo
    // ...foo → 3 dots → pop 2 → go up two dirs, append foo
    let pop_count = dots.saturating_sub(1);
    for _ in 0..pop_count {
        segments.pop();
    }

    if !rest.is_empty() {
        segments.push(rest);
    }

    let module = segments.join(".");
    strip_source_root_prefix(&module)
}

/// Strip common source root prefixes from a dotted module name.
///
/// Mirrors the logic in `resolver::module_index::strip_src_prefix`.
/// Prefixes like `src.`, `lib.`, `test.`, `tests.` are not part of the
/// Python package hierarchy — they are source directory conventions.
fn strip_source_root_prefix(module: &str) -> String {
    for prefix in &["src.", "lib.", "test.", "tests."] {
        if module.starts_with(prefix) {
            return module[prefix.len()..].to_string();
        }
    }
    module.to_string()
}

/// Qualify a dotted chain (e.g. `mylib.helper`) by replacing the first segment
/// with its imported qualified name if it exists in `imported_names`.
///
/// Example: if `imported_names` maps `"mylib" → "package.mylib"`, then
/// `qualify_chain(..., "mylib.helper")` returns `"package.mylib.helper"`.
fn qualify_chain(imported_names: &HashMap<String, String>, chain: &str) -> String {
    if let Some(dot_pos) = chain.find('.') {
        let first = &chain[..dot_pos];
        if let Some(qualified) = imported_names.get(first) {
            return format!("{}.{}", qualified, &chain[dot_pos + 1..]);
        }
    }
    chain.to_string()
}

/// Qualify a type annotation text by looking up imported names.
///
/// Two cases:
/// 1. Simple type name: `MyType` — if in `imported_names`, use qualified form
/// 2. Dotted type name: `module.MyType` — if `module` is in `imported_names`,
///    qualify the chain
/// 3. Everything else (generics `List[X]`, complex expressions) — return as-is
fn qualify_type_text(imported_names: &HashMap<String, String>, type_text: &str) -> String {
    // Skip complex type expressions with brackets, quotes, etc.
    if type_text.contains('[') || type_text.contains('"') || type_text.contains('\'') {
        return type_text.to_string();
    }
    // Check if the whole type_text is an imported name
    if let Some(qualified) = imported_names.get(type_text) {
        return qualified.clone();
    }
    // Check if it's a dotted name where the first segment is imported
    if type_text.contains('.') {
        return qualify_chain(imported_names, type_text);
    }
    type_text.to_string()
}

/// Build a target qualified name for a call edge.
fn build_call_target(
    file_path: &str,
    class_stack: &[String],
    callee: &str,
    is_self_call: bool,
) -> String {
    if is_self_call {
        if let Some(class_name) = class_stack.last() {
            return format!("{file_path}::{class_name}.{callee}");
        }
    }
    format!("{file_path}::{callee}")
}

/// Get the UTF-8 text of a node from the source bytes.
fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
    }
}

/// Resolve an attribute chain like `os.path.join` to a dotted string.
fn resolve_attribute_chain(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();

    match node.kind() {
        "identifier" => {
            return get_text(source, Some(node));
        }
        "attribute" => {
            if let Some(attr) = node.child_by_field_name("attribute") {
                parts.push(get_text(source, Some(attr)));
            }
            let mut current = node.child_by_field_name("object");
            loop {
                match current {
                    Some(obj) => match obj.kind() {
                        "identifier" => {
                            parts.push(get_text(source, Some(obj)));
                            break;
                        }
                        "attribute" => {
                            if let Some(a) = obj.child_by_field_name("attribute") {
                                parts.push(get_text(source, Some(a)));
                            }
                            current = obj.child_by_field_name("object");
                        }
                        "call" => {
                            if let Some(f) = obj.child_by_field_name("function") {
                                parts.push(format!(
                                    "{}()",
                                    resolve_attribute_chain(source, f)
                                ));
                            }
                            break;
                        }
                        _ => break,
                    },
                    None => break,
                }
            }
        }
        "call" => {
            if let Some(f) = node.child_by_field_name("function") {
                return resolve_attribute_chain(source, f) + "()";
            }
        }
        _ => {
            return get_text(source, Some(node));
        }
    }

    parts.reverse();
    parts.join(".")
}

/// Check if a string consists of ALL_CAPS with underscores and digits.
fn is_all_caps(s: &str) -> bool {
    if s.is_empty() {
        return false;
    }
    let has_alpha = s.chars().any(|c| c.is_alphabetic());
    if !has_alpha {
        return false;
    }
    s.chars()
        .all(|c| c.is_uppercase() || c == '_' || c.is_ascii_digit())
}

/// Extract the docstring from a function/class body.
fn extract_docstring(source: &[u8], body: Node) -> Option<String> {
    if let Some(first) = body.named_child(0) {
        if first.kind() == "expression_statement" {
            if let Some(inner) = first.named_child(0) {
                if inner.kind() == "string" {
                    let text = get_text(source, Some(inner));
                    if !text.is_empty() {
                        let trimmed = text.trim_matches(|c| c == '"' || c == '\'');
                        let truncated: String = trimmed.chars().take(200).collect();
                        return Some(truncated);
                    }
                }
            }
        }
    }
    None
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

    /// Parse Python source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("set python language");
        let tree = parser.parse(source, None).expect("parse python source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "python".to_string());
        PythonExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes(
        ctx: &ExtractionContext,
        kind: NodeKind,
    ) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    /// Helper: find edges of a given kind.
    fn find_edges<'a>(
        ctx: &'a ExtractionContext,
        kind: EdgeKind,
    ) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result
            .edges
            .iter()
            .filter(|e| e.kind == kind_str)
            .collect()
    }

    // Re-export for tests
    fn node_kind_to_str(kind: NodeKind) -> &'static str {
        crate::indexer::context::node_kind_to_str(kind)
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract("class MyClass:\n    pass\n", "src/test.py");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_inheritance() {
        let ctx = extract("class Child(BaseClass, Mixin):\n    pass\n", "src/test.py");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Child");

        let extends_edges = find_edges(&ctx, EdgeKind::Extends);
        assert_eq!(extends_edges.len(), 2);
        let target_texts: Vec<&str> =
            extends_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(target_texts.contains(&"BaseClass"));
        assert!(target_texts.contains(&"Mixin"));
    }

    #[test]
    fn test_extract_class_with_contains_edge() {
        let ctx = extract("class MyClass:\n    pass\n", "src/test.py");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty());
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_module_function() {
        let ctx = extract("def my_func(x, y):\n    return x + y\n", "src/test.py");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "my_func");
    }

    #[test]
    fn test_extract_method_inside_class() {
        let ctx = extract("class MyClass:\n    def my_method(self):\n        pass\n", "src/test.py");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "my_method");
    }

    #[test]
    fn test_extract_multiple_methods() {
        let src = "class MyClass:\n    def method_a(self):\n        pass\n    def method_b(self):\n        pass\n";
        let ctx = extract(src, "src/test.py");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 2);
        let names: Vec<&str> = methods.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"method_a"));
        assert!(names.contains(&"method_b"));
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract("def foo():\n    bar()\n", "src/test.py");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"bar"), "Expected 'bar' in call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_self_method_call() {
        let ctx = extract(
            "class MyClass:\n    def method_a(self):\n        self.method_b()\n",
            "src/test.py",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least one call edge");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("method_b")), "Expected self.method_b in: {:?}", targets);
    }

    #[test]
    fn test_extract_chained_call() {
        let ctx = extract("def foo():\n    obj.method().another()\n", "src/test.py");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least one call edge for chained calls");
    }

    #[test]
    fn test_filter_python_builtins() {
        let ctx = extract("def foo():\n    print('hello')\n    len(x)\n    isinstance(x, int)\n", "src/test.py");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.contains(&"print"), "Should filter print");
        assert!(!targets.contains(&"len"), "Should filter len");
        assert!(!targets.contains(&"isinstance"), "Should filter isinstance");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_import_statement() {
        let ctx = extract("import os\nimport sys, json\n", "src/test.py");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 3);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"os"));
        assert!(targets.contains(&"sys"));
        assert!(targets.contains(&"json"));
    }

    #[test]
    fn test_extract_import_from_statement() {
        let ctx = extract("from os.path import join, exists\n", "src/test.py");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].target_text.as_deref().unwrap_or(""), "os.path");
    }

    #[test]
    fn test_import_from_creates_references_edges() {
        // from X import Y → REFERENCE edge with target_text = "X.Y"
        let ctx = extract("from utils import helper\n", "src/test.py");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected at least one REFERENCE edge");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"utils.helper"), "Expected 'utils.helper' in REFERENCE targets: {:?}", targets);
    }

    #[test]
    fn test_import_from_multiple_symbols_references() {
        // from X import Y, Z → REFERENCE edges for both Y and Z
        let ctx = extract("from mylib import func_a, func_b\n", "src/test.py");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(refs.len() >= 2, "Expected at least 2 REFERENCE edges, got {}", refs.len());
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"mylib.func_a"), "Expected 'mylib.func_a' in: {:?}", targets);
        assert!(targets.contains(&"mylib.func_b"), "Expected 'mylib.func_b' in: {:?}", targets);
    }

    #[test]
    fn test_import_from_aliased_creates_references() {
        // from X import Y as ALIAS → REFERENCE edge with target_text = "X.Y" (original name)
        let ctx = extract("from utils import helper as hlp\n", "src/test.py");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected at least one REFERENCE edge for aliased import");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"utils.helper"), "Expected 'utils.helper' (original name) in: {:?}", targets);
        assert!(!targets.contains(&"utils.hlp"), "Alias should NOT appear in REFERENCE target_text");
    }

    #[test]
    fn test_import_as_alias() {
        // import X as Y → IMPORTS edge + imported_names maps alias → module
        let ctx = extract("import numpy as np\n", "src/test.py");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].target_text.as_deref().unwrap_or(""), "numpy");
    }

    #[test]
    fn test_imported_call_uses_qualified_target_text() {
        // When a call target was imported via `from X import Y`,
        // the CALLS edge should use qualified target_text = "X.Y"
        let ctx = extract(
            "from utils import helper\ndef foo():\n    helper()\n",
            "src/test.py",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"utils.helper"), "Expected 'utils.helper' in call targets: {:?}", targets);
    }

    #[test]
    fn test_imported_alias_call_uses_original_qualified_name() {
        // from X import Y as Z → call Z() should use target_text = "X.Y"
        let ctx = extract(
            "from utils import helper as hlp\ndef foo():\n    hlp()\n",
            "src/test.py",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"utils.helper"), "Expected 'utils.helper' (original name) in: {:?}", targets);
    }

    #[test]
    fn test_imported_type_annotation_uses_qualified_target_text() {
        // Type annotations referencing imported types should use qualified target_text
        let ctx = extract(
            "from models import User\ndef get_user() -> User:\n    pass\n",
            "src/test.py",
        );
        let type_refs: Vec<&crate::db::models::EdgeRecord> =
            ctx.result.edges.iter().filter(|e| e.kind == "TYPE_REF").collect();
        assert!(type_refs.len() >= 1, "Expected at least one TYPE_REF edge, got {}", type_refs.len());
        let texts: Vec<&str> = type_refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(texts.contains(&"models.User"), "Expected 'models.User' in TYPE_REF: {:?}", texts);
    }

    #[test]
    fn test_imported_type_param_annotation_qualified() {
        // Type annotations in parameters should use qualified target_text
        let ctx = extract(
            "from schemas import Item\ndef process(x: Item) -> None:\n    pass\n",
            "src/test.py",
        );
        let type_refs: Vec<&crate::db::models::EdgeRecord> =
            ctx.result.edges.iter().filter(|e| e.kind == "TYPE_REF").collect();
        let texts: Vec<&str> = type_refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(texts.iter().any(|t| *t == "schemas.Item"), "Expected 'schemas.Item' in TYPE_REF: {:?}", texts);
    }

    #[test]
    fn test_non_imported_call_uses_bare_name() {
        // Calls to non-imported (local) symbols should still use bare name
        let ctx = extract(
            "def local_func():\n    pass\ndef foo():\n    local_func()\n",
            "src/test.py",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"local_func"), "Expected 'local_func' in call targets: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Decorator extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_decorated_function() {
        let ctx = extract("@staticmethod\ndef my_func():\n    pass\n", "src/test.py");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "my_func");
    }

    #[test]
    fn test_extract_decorated_method() {
        let ctx = extract(
            "class MyClass:\n    @property\n    def my_method(self):\n        pass\n",
            "src/test.py",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
    }

    // ------------------------------------------------------------------
    // Variable / constant extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_module_constant() {
        let ctx = extract("MAX_SIZE = 100\nDEFAULT_TIMEOUT = 30\n", "src/test.py");
        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert_eq!(constants.len(), 2);
        let names: Vec<&str> = constants.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"MAX_SIZE"));
        assert!(names.contains(&"DEFAULT_TIMEOUT"));
    }

    #[test]
    fn test_extract_dunder_all() {
        let ctx = extract("__all__ = ['foo', 'bar']\n", "src/test.py");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "__all__");
    }

    #[test]
    fn test_no_constant_for_lowercase() {
        let ctx = extract("my_var = 42\nnormal_name = 'hello'\n", "src/test.py");
        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert!(constants.is_empty(), "Lowercase variables should not be constants");
    }

    // ------------------------------------------------------------------
    // Docstring extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_docstring() {
        let ctx = extract(
            "def my_func():\n    \"\"\"This is a docstring.\"\"\"\n    pass\n",
            "src/test.py",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        if let Some(doc) = &funcs[0].docstring {
            assert!(doc.contains("This is a docstring"));
        }
    }

    // ------------------------------------------------------------------
    // Type annotation extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_custom_type_annotation() {
        let ctx = extract(
            "class MyType:\n    pass\n\ndef my_func(x: MyType) -> MyType:\n    return x\n",
            "src/test.py",
        );
        let type_refs: Vec<&crate::db::models::EdgeRecord> =
            ctx.result.edges.iter().filter(|e| e.kind == "TYPE_REF").collect();
        assert!(type_refs.len() >= 2, "Expected at least 2 TYPE_REF edges, got {}", type_refs.len());
        let texts: Vec<&str> = type_refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(texts.iter().any(|t| *t == "MyType"));
    }

    // ------------------------------------------------------------------
    // Env access
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_os_getenv() {
        let ctx = extract(
            "import os\ndef foo():\n    val = os.getenv('HOME')\n",
            "src/test.py",
        );
        let env_edges: Vec<&crate::db::models::EdgeRecord> =
            ctx.result.edges.iter().filter(|e| e.kind == "ENV_ACCESSES").collect();
        assert!(!env_edges.is_empty(), "Expected ENV_ACCESSES edge for os.getenv");
    }

    #[test]
    fn test_extract_os_environ_subscript() {
        let ctx = extract(
            "import os\ndef foo():\n    val = os.environ['HOME']\n",
            "src/test.py",
        );
        let env_edges: Vec<&crate::db::models::EdgeRecord> =
            ctx.result.edges.iter().filter(|e| e.kind == "ENV_ACCESSES").collect();
        assert!(!env_edges.is_empty(), "Expected ENV_ACCESSES for os.environ subscript");
    }

    // ------------------------------------------------------------------
    // ABCMeta (implements) detection
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_abcmeta_class() {
        let ctx = extract(
            "from abc import ABCMeta\nclass MyClass(metaclass=ABCMeta):\n    pass\n",
            "src/test.py",
        );
        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert!(!implements.is_empty(), "Expected IMPLEMENTS edge for ABCMeta");
        assert!(implements.iter().any(|e| e.target_text.as_deref().map_or(false, |t| t.contains("ABCMeta"))));
    }

    // ------------------------------------------------------------------
    // Module-level expression (standalone call)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_module_level_call() {
        let ctx = extract("setup_logging()\nregister_handlers()\n", "src/test.py");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"setup_logging"), "Expected setup_logging in {:?}", targets);
        assert!(targets.contains(&"register_handlers"), "Expected register_handlers in {:?}", targets);
    }

    // ------------------------------------------------------------------
    // File node
    // ------------------------------------------------------------------

    #[test]
    fn test_file_node_exists() {
        let ctx = extract("pass\n", "src/test.py");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Complex nested structures
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_class_in_function() {
        let ctx = extract(
            "def outer():\n    class Inner:\n        def method(self):\n            pass\n",
            "src/test.py",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Inner");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
    }

    #[test]
    fn test_call_with_args() {
        let ctx = extract("def foo():\n    bar(1, 2, key='value')\n", "src/test.py");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"bar"));
    }

    // ------------------------------------------------------------------
    // Regression: ensure we don't crash on empty / edge-case files
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.py");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("# just a comment\n", "src/comments.py");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Relative import resolution (resolve_relative_import)
    // ------------------------------------------------------------------

    #[test]
    fn test_resolve_relative_import_dot_sibling() {
        // .foo in src/pkg/subpkg/module.py → pkg.subpkg.foo
        let result = resolve_relative_import(".foo", "src/pkg/subpkg/module.py");
        assert_eq!(result, "pkg.subpkg.foo");
    }

    #[test]
    fn test_resolve_relative_import_dotdot_sibling() {
        // ..sibling in src/pkg/subpkg/module.py → pkg.sibling
        let result = resolve_relative_import("..sibling", "src/pkg/subpkg/module.py");
        assert_eq!(result, "pkg.sibling");
    }

    #[test]
    fn test_resolve_relative_import_triple_dot() {
        // ...grandparent in src/pkg/subpkg/module.py → grandparent
        let result = resolve_relative_import("...grandparent", "src/pkg/subpkg/module.py");
        assert_eq!(result, "grandparent");
    }

    #[test]
    fn test_resolve_relative_import_dot_only() {
        // . alone in src/pkg/module.py → pkg (current package)
        let result = resolve_relative_import(".", "src/pkg/module.py");
        assert_eq!(result, "pkg");
    }

    #[test]
    fn test_resolve_relative_import_non_relative_passthrough() {
        // Regular absolute import should pass through unchanged
        let result = resolve_relative_import("os.path", "src/pkg/module.py");
        assert_eq!(result, "os.path");
    }

    #[test]
    fn test_resolve_relative_import_windows_path() {
        // Should also work with Windows backslash paths
        let result = resolve_relative_import("..sibling", r"src\pkg\subpkg\module.py");
        assert_eq!(result, "pkg.sibling");
    }

    // ------------------------------------------------------------------
    // Relative import from extractor integration
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_relative_import_dot() {
        // from .foo import Bar → IMPORTS + REFERENCES with resolved absolute module
        let ctx = extract(
            "from .foo import Bar\n",
            "src/pkg/subpkg/module.py",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(
            imports[0].target_text.as_deref().unwrap_or(""),
            "pkg.subpkg.foo"
        );

        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "pkg.subpkg.foo.Bar"
        );
    }

    #[test]
    fn test_extract_relative_import_dotdot() {
        // from ..sibling import Baz → IMPORTS + REFERENCES with resolved absolute module
        let ctx = extract(
            "from ..sibling import Baz\n",
            "src/pkg/subpkg/module.py",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(
            imports[0].target_text.as_deref().unwrap_or(""),
            "pkg.sibling"
        );

        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "pkg.sibling.Baz"
        );
    }

    #[test]
    fn test_extract_relative_import_with_alias() {
        // from .foo import Bar as B → REFERENCES target_text = "pkg.subpkg.foo.Bar"
        let ctx = extract(
            "from .foo import Bar as B\n",
            "src/pkg/subpkg/module.py",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "pkg.subpkg.foo.Bar"
        );
    }

    #[test]
    fn test_extract_relative_import_qualified_call() {
        // Imported symbol from relative import should use qualified target_text in CALLS
        let ctx = extract(
            "from .utils import helper\ndef foo():\n    helper()\n",
            "src/pkg/subpkg/module.py",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1);
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "pkg.subpkg.utils.helper"
        );

        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.contains(&"pkg.subpkg.utils.helper"),
            "Expected 'pkg.subpkg.utils.helper' in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_non_relative_import_unaffected() {
        // Non-relative imports should still work as before
        let ctx = extract(
            "from os.path import join\nfrom utils import helper\n",
            "src/test.py",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 2);
        let targets: Vec<&str> =
            imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"os.path"));
        assert!(targets.contains(&"utils"));

        let refs = find_edges(&ctx, EdgeKind::References);
        let ref_targets: Vec<&str> =
            refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(ref_targets.contains(&"os.path.join"));
        assert!(ref_targets.contains(&"utils.helper"));
    }
}
