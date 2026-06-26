//! Graph traverser — BFS-based path finding and impact radius computation.

use std::collections::{HashMap, HashSet, VecDeque};

/// A simple BFS graph traverser.
pub struct GraphTraverser {
    adjacency: HashMap<i64, Vec<(i64, String)>>,
}

impl GraphTraverser {
    /// Build a traverser from edge lists.
    pub fn new(edges: &[(i64, i64, String)]) -> Self {
        let mut adjacency: HashMap<i64, Vec<(i64, String)>> = HashMap::new();
        for (src, tgt, kind) in edges {
            adjacency.entry(*src).or_default().push((*tgt, kind.clone()));
        }
        Self { adjacency }
    }

    /// Find all nodes reachable from `start` within `max_depth` hops.
    pub fn impact_radius(&self, start: i64, max_depth: usize) -> HashSet<i64> {
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back((start, 0));
        visited.insert(start);

        while let Some((node, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }
            if let Some(neighbors) = self.adjacency.get(&node) {
                for (nbr, _kind) in neighbors {
                    if visited.insert(*nbr) {
                        queue.push_back((*nbr, depth + 1));
                    }
                }
            }
        }

        visited
    }

    /// Find the shortest path between `src` and `tgt` (BFS), if any.
    pub fn shortest_path(&self, src: i64, tgt: i64) -> Option<Vec<i64>> {
        let mut queue = VecDeque::new();
        let mut parent: HashMap<i64, i64> = HashMap::new();
        let mut visited = HashSet::new();

        queue.push_back(src);
        visited.insert(src);

        while let Some(node) = queue.pop_front() {
            if node == tgt {
                // Reconstruct path
                let mut path = vec![tgt];
                let mut cur = tgt;
                while cur != src {
                    cur = parent[&cur];
                    path.push(cur);
                }
                path.reverse();
                return Some(path);
            }

            if let Some(neighbors) = self.adjacency.get(&node) {
                for (nbr, _) in neighbors {
                    if !visited.contains(nbr) {
                        visited.insert(*nbr);
                        parent.insert(*nbr, node);
                        queue.push_back(*nbr);
                    }
                }
            }
        }

        None
    }
}
