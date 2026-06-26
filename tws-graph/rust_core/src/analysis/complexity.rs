//! Complexity analysis — cyclomatic, cognitive, and Halstead metrics.

/// Complexity metrics for a single function / method.
#[derive(Debug, Default)]
pub struct ComplexityMetrics {
    pub cyclomatic: u32,
    pub cognitive: u32,
    pub halstead_volume: f64,
    pub lines: u32,
}

/// Placeholder for complexity calculation.
pub fn analyze(_source: &str) -> ComplexityMetrics {
    ComplexityMetrics::default()
}
