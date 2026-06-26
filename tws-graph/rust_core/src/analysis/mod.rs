//! Code analysis — dead code detection, entry points, complexity, test edges,
//! config links, git diff impact, health scoring, and impact prediction.

pub mod complexity;
pub mod config_links;
pub mod dead_code;
pub mod entry_point;
pub mod git_diff;
pub mod health;
pub mod impact_prediction;
pub mod test_edges;
