//! Code health scoring — combines test coverage, dead code, and coupling.

/// Health score for a single file.
#[derive(Debug, Default)]
pub struct HealthScore {
    pub file_path: String,
    pub score: f64,
    pub test_coverage_pct: f64,
    pub dead_code_count: u32,
    pub coupling_score: f64,
}

/// Placeholder for health scoring.
pub fn score_all() -> Vec<HealthScore> {
    Vec::new()
}
