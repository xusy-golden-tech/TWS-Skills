//! Code analysis — cycle detection, layer violation detection, module metrics,
//! taint analysis, dead code detection, entry points, complexity, test edges,
//! config links, clone detection, community detection, centrality metrics,
//! git diff impact, health scoring, and impact prediction.

pub mod centrality;
pub mod clone;
pub mod community;
pub mod complexity;
pub mod config_links;
pub mod cycles;
pub mod dead_code;
pub mod entry_point;
pub mod git_diff;
pub mod health;
pub mod impact_prediction;
pub mod layers;
pub mod metrics;
pub mod taint;
pub mod test_edges;

// Re-export key types and functions
pub use centrality::{compute_betweenness, compute_pagerank};
pub use clone::find_clones;
pub use community::detect_communities;
pub use complexity::{analyze_complexity, ComplexityMetrics};
pub use config_links::find_config_links;
pub use cycles::detect_cycles;
pub use dead_code::find_dead_code;
pub use entry_point::{find_entry_points, EntryPoint};
pub use git_diff::analyze_git_diff;
pub use health::compute_health;
pub use impact_prediction::predict_impact;
pub use layers::detect_layer_violations;
pub use metrics::{compute_metrics, ModuleMetrics};
pub use taint::taint_analysis;
pub use test_edges::find_test_edges;
