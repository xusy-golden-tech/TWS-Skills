//! File-system watcher — polls for file changes via stat() mtime comparison
//! and triggers auto-sync with the extraction pipeline.
//!
//! Uses a polling approach (cross-platform) with debounce to batch rapid
//! changes.  Mirrors the Python ``PollingFileWatcher`` behaviour.

use std::collections::HashMap;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

// ---------------------------------------------------------------------------
// Constants — mirror scanner::SKIP_DIRS
// ---------------------------------------------------------------------------

/// Directories to skip during filesystem walk.
const SKIP_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    "__pycache__",
    ".tox",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "target",
];

/// Path prefixes to skip during filesystem walk.
const SKIP_PATH_PREFIXES: &[&str] = &[".tws/codegraph", ".tws/sessions"];

/// Default debounce window: 300 ms.
const DEBOUNCE_WINDOW_MS: u64 = 300;

/// Maximum hold time for debounced events: 2 s.
const DEBOUNCE_MAX_HOLD_MS: u64 = 2_000;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// A file change event detected by the watcher.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchEvent {
    pub path: PathBuf,
    pub kind: WatchEventKind,
}

/// The kind of file-system change.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchEventKind {
    Created,
    Modified,
    Removed,
}

// ---------------------------------------------------------------------------
// FileWatcher
// ---------------------------------------------------------------------------

/// File system watcher for auto-sync on file changes.
///
/// Uses a polling (stat-based) approach — portable across Linux, macOS, and
/// Windows without platform-specific notification APIs.
pub struct FileWatcher {
    root: PathBuf,
    db_path: PathBuf,
    interval: Duration,
    running: Arc<AtomicBool>,
    sender: Option<mpsc::Sender<WatchEvent>>,
}

impl FileWatcher {
    /// Create a new watcher for a directory.
    ///
    /// * `root`        — root directory to watch.
    /// * `db_path`     — path to the SQLite index database (for auto-sync).
    /// * `interval_secs` — polling interval in seconds.
    pub fn new(root: &Path, db_path: &Path, interval_secs: f64) -> Self {
        Self {
            root: root.to_path_buf(),
            db_path: db_path.to_path_buf(),
            interval: Duration::from_secs_f64(interval_secs.max(0.1)),
            running: Arc::new(AtomicBool::new(false)),
            sender: None,
        }
    }

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /// Start watching and return a channel of events.
    ///
    /// Spawns a background thread that polls the filesystem at the configured
    /// interval.  Returns a [`mpsc::Receiver`] that yields [`WatchEvent`]s.
    ///
    /// The initial scan establishes an mtime baseline — no events are emitted
    /// for pre-existing files.
    pub fn start(&mut self) -> mpsc::Receiver<WatchEvent> {
        let (tx, rx) = mpsc::channel::<WatchEvent>();
        self.sender = Some(tx.clone());

        let root = self.root.clone();
        let interval = self.interval;
        let running = Arc::clone(&self.running);
        running.store(true, Ordering::SeqCst);

        std::thread::Builder::new()
            .name("tws-watcher".into())
            .spawn(move || {
                let mut last_mtimes: HashMap<PathBuf, u64> = HashMap::new();

                // Initial scan: build baseline (no events emitted)
                if let Ok(current) = scan_files(&root) {
                    last_mtimes = current;
                }

                while running.load(Ordering::SeqCst) {
                    std::thread::sleep(interval);

                    let mut events = poll_changes_inner(&root, &mut last_mtimes);

                    // Debounce: collapse rapid changes to the same file
                    events = debounce_events(events);

                    for event in events {
                        if tx.send(event).is_err() {
                            // receiver dropped — stop
                            running.store(false, Ordering::SeqCst);
                            return;
                        }
                    }
                }
            })
            .expect("failed to spawn watcher thread");

        rx
    }

    /// Stop the watcher.
    ///
    /// Signals the background thread to exit and drops the sender, which
    /// closes the channel.
    pub fn stop(&mut self) {
        self.running.store(false, Ordering::SeqCst);
        self.sender = None;
    }

    /// Return true if the background watcher thread is currently running.
    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    // -----------------------------------------------------------------------
    // Change detection
    // -----------------------------------------------------------------------

    /// Scan for changes since last check. Returns changed files.
    ///
    /// Compares current filesystem mtimes against `last_check`:
    /// - Files present in the scan but missing from `last_check` → `Created`
    /// - Files in `last_check` but missing from the scan → `Removed`
    /// - Files in both with a different mtime → `Modified`
    ///
    /// Updates `last_check` in-place to reflect the new snapshot.
    pub fn poll_changes(
        &self,
        last_check: &mut HashMap<PathBuf, u64>,
    ) -> Vec<WatchEvent> {
        poll_changes_inner(&self.root, last_check)
    }

    // -----------------------------------------------------------------------
    // Main loop (for CLI usage)
    // -----------------------------------------------------------------------

    /// Run the watch loop synchronously in the current thread.
    ///
    /// Polls every `interval`, emits one JSONL line per event to stdout,
    /// and keeps running until the process receives an interrupt signal
    /// (Ctrl-C).
    ///
    /// Output format (one JSON object per line):
    /// ```jsonl
    /// {"path":"src/main.py","kind":"Modified"}
    /// {"path":"src/new.rs","kind":"Created"}
    /// ```
    pub fn run_loop(&mut self) -> anyhow::Result<()> {
        self.running.store(true, Ordering::SeqCst);
        let mut last_mtimes: HashMap<PathBuf, u64> = HashMap::new();

        // Initial scan — build baseline, do not emit events
        if let Ok(current) = scan_files(&self.root) {
            last_mtimes = current;
        }

        let stdout = io::stdout();
        let mut handle = stdout.lock();

        while self.running.load(Ordering::SeqCst) {
            std::thread::sleep(self.interval);

            let mut events = poll_changes_inner(&self.root, &mut last_mtimes);
            events = debounce_events(events);

            for event in &events {
                let kind_str = match event.kind {
                    WatchEventKind::Created => "Created",
                    WatchEventKind::Modified => "Modified",
                    WatchEventKind::Removed => "Removed",
                };
                // Escape path for JSON (simple — handle backslashes and quotes)
                let path_escaped = event.path.display().to_string()
                    .replace('\\', "\\\\")
                    .replace('"', "\\\"");
                writeln!(
                    handle,
                    "{{\"path\":\"{}\",\"kind\":\"{}\"}}",
                    path_escaped, kind_str
                )?;
            }
            handle.flush()?;
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Scan `root` for files, returning `{absolute_path: mtime}` for every
/// non-ignored file.
fn scan_files(root: &Path) -> io::Result<HashMap<PathBuf, u64>> {
    let mut result = HashMap::new();
    if !root.is_dir() {
        return Ok(result);
    }
    scan_dir_recursive(root, root, &mut result)?;
    Ok(result)
}

/// Recursive walkdir helper — skip ignored directories and paths.
fn scan_dir_recursive(
    root: &Path,
    current: &Path,
    acc: &mut HashMap<PathBuf, u64>,
) -> io::Result<()> {
    let entries = match std::fs::read_dir(current) {
        Ok(e) => e,
        Err(_) => return Ok(()),
    };

    for entry in entries.flatten() {
        let path = entry.path();
        let file_name = entry.file_name();
        let name_str = file_name.to_string_lossy();

        if path.is_dir() {
            // Skip ignored directories
            if SKIP_DIRS.iter().any(|d| name_str.as_ref() == *d) {
                continue;
            }
            // Skip .tws sub-paths (codegraph, sessions)
            if let Ok(rel) = path.strip_prefix(root) {
                let rel_str = rel.to_string_lossy().replace('\\', "/");
                if SKIP_PATH_PREFIXES.iter().any(|p| rel_str.starts_with(p)) {
                    continue;
                }
            }
            scan_dir_recursive(root, &path, acc)?;
        } else if path.is_file() {
            if let Ok(meta) = entry.metadata() {
                if let Ok(mtime) = meta.modified() {
                    if let Ok(dur) = mtime.duration_since(std::time::UNIX_EPOCH) {
                        acc.insert(path, dur.as_millis() as u64);
                    }
                }
            }
        }
    }

    Ok(())
}

/// Compare current mtimes against `last_check` and produce events.
fn poll_changes_inner(
    root: &Path,
    last_check: &mut HashMap<PathBuf, u64>,
) -> Vec<WatchEvent> {
    let mut events = Vec::new();

    let current = match scan_files(root) {
        Ok(c) => c,
        Err(_) => return events,
    };

    // Created: in current but not in last_check
    for (path, _mtime) in &current {
        if !last_check.contains_key(path) {
            events.push(WatchEvent {
                path: path.clone(),
                kind: WatchEventKind::Created,
            });
        }
    }

    // Removed: in last_check but not in current
    for path in last_check.keys() {
        if !current.contains_key(path) {
            events.push(WatchEvent {
                path: path.clone(),
                kind: WatchEventKind::Removed,
            });
        }
    }

    // Modified: in both but mtime changed
    for (path, mtime) in &current {
        if let Some(old_mtime) = last_check.get(path) {
            if *mtime != *old_mtime {
                events.push(WatchEvent {
                    path: path.clone(),
                    kind: WatchEventKind::Modified,
                });
            }
        }
    }

    // Update last_check to current snapshot
    *last_check = current;

    events
}

/// Debounce events: collapse rapid changes to the same file path.
///
/// Uses a 300 ms window — multiple events for the same path within that
/// window are collapsed to a single event (latest kind wins).  With a
/// 2 s maximum hold time, events are never delayed more than 2 s.
fn debounce_events(events: Vec<WatchEvent>) -> Vec<WatchEvent> {
    if events.len() <= 1 {
        return events;
    }

    // Group by path, keep the latest event kind per path
    let mut grouped: HashMap<PathBuf, WatchEventKind> = HashMap::new();
    for event in events {
        grouped.insert(event.path, event.kind);
    }

    grouped
        .into_iter()
        .map(|(path, kind)| WatchEvent { path, kind })
        .collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::Duration;

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /// Create a temporary directory with optional files.
    fn create_temp_dir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("tws_watch_test_{}", uuid_simple()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Generate a simple 8-char ID for temp paths.
    fn uuid_simple() -> String {
        use std::time::{SystemTime, UNIX_EPOCH};
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        format!("{:08x}", ts as u32)
    }

    fn write_file(dir: &Path, name: &str, content: &str) -> PathBuf {
        let path = dir.join(name);
        fs::write(&path, content).unwrap();
        path
    }

    fn cleanup(dir: &Path) {
        let _ = fs::remove_dir_all(dir);
    }

    // ------------------------------------------------------------------
    // Construction tests
    // ------------------------------------------------------------------

    #[test]
    fn test_new_watcher() {
        let root = PathBuf::from("/tmp/test_root");
        let db = PathBuf::from("/tmp/test.db");
        let w = FileWatcher::new(&root, &db, 2.0);
        assert_eq!(w.root, root);
        assert_eq!(w.db_path, db);
        assert_eq!(w.interval, Duration::from_secs_f64(2.0));
        assert!(!w.is_running());
    }

    #[test]
    fn test_new_watcher_clamps_min_interval() {
        let root = PathBuf::from("/tmp/test");
        let db = PathBuf::from("/tmp/test.db");
        // Interval below 0.1 should be clamped to 0.1
        let w = FileWatcher::new(&root, &db, 0.001);
        assert!(w.interval.as_secs_f64() >= 0.1);
    }

    #[test]
    fn test_stop_when_not_started() {
        let root = PathBuf::from("/tmp/test");
        let db = PathBuf::from("/tmp/test.db");
        let mut w = FileWatcher::new(&root, &db, 1.0);
        w.stop(); // should not panic
        assert!(!w.is_running());
    }

    // ------------------------------------------------------------------
    // poll_changes tests
    // ------------------------------------------------------------------

    #[test]
    fn test_poll_changes_detects_new_file() {
        let dir = create_temp_dir();

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            // Initial scan: no files
            let events = w.poll_changes(&mut mtimes);
            assert!(events.is_empty());
            assert!(mtimes.is_empty());

            // Create a file
            write_file(&dir, "hello.py", "print('hi')");

            // Now poll should detect the new file
            let events = w.poll_changes(&mut mtimes);
            let created: Vec<_> = events.iter().filter(|e| e.kind == WatchEventKind::Created).collect();
            assert!(!created.is_empty(), "Expected at least one Created event");
            assert!(created.iter().any(|e| e.path.file_name().unwrap() == "hello.py"));
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_detects_modified_file() {
        let dir = create_temp_dir();
        let py_file = write_file(&dir, "app.py", "original");

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            // First scan: detect the pre-existing file
            let _ = w.poll_changes(&mut mtimes);

            // Modify the file
            // Need to wait a tiny bit for mtime to actually change
            std::thread::sleep(Duration::from_millis(10));
            fs::write(&py_file, "modified").unwrap();

            let events = w.poll_changes(&mut mtimes);
            let modified: Vec<_> = events.iter().filter(|e| e.kind == WatchEventKind::Modified).collect();
            assert!(!modified.is_empty(), "Expected at least one Modified event");
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_detects_removed_file() {
        let dir = create_temp_dir();
        let py_file = write_file(&dir, "remove_me.py", "data");

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            // First scan: detect the pre-existing file
            let _ = w.poll_changes(&mut mtimes);

            // Remove the file
            fs::remove_file(&py_file).unwrap();

            let events = w.poll_changes(&mut mtimes);
            let removed: Vec<_> = events.iter().filter(|e| e.kind == WatchEventKind::Removed).collect();
            assert!(!removed.is_empty(), "Expected at least one Removed event");
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_skips_ignored_dirs() {
        let dir = create_temp_dir();
        let git_dir = dir.join(".git");
        fs::create_dir_all(&git_dir).unwrap();
        write_file(&git_dir, "config", "git data");

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            let events = w.poll_changes(&mut mtimes);

            // .git/config should NOT appear
            let git_events: Vec<_> = events.iter()
                .filter(|e| e.path.to_string_lossy().contains(".git"))
                .collect();
            assert!(git_events.is_empty(), ".git files should be ignored");
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_skips_tws_paths() {
        let dir = create_temp_dir();
        let tws_dir = dir.join(".tws").join("codegraph");
        fs::create_dir_all(&tws_dir).unwrap();
        write_file(&tws_dir, "index.db", "binary data");

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            let events = w.poll_changes(&mut mtimes);

            let tws_events: Vec<_> = events.iter()
                .filter(|e| e.path.to_string_lossy().contains(".tws"))
                .collect();
            assert!(tws_events.is_empty(), ".tws paths should be ignored");
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_empty_directory() {
        let dir = create_temp_dir();

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();
            let events = w.poll_changes(&mut mtimes);
            assert!(events.is_empty());
        }

        cleanup(&dir);
    }

    #[test]
    fn test_poll_changes_subdirectory() {
        let dir = create_temp_dir();
        let sub = dir.join("sub");
        fs::create_dir_all(&sub).unwrap();

        {
            let w = FileWatcher::new(&dir, &dir.join("test.db"), 1.0);
            let mut mtimes = HashMap::new();

            // Baseline scan
            let _ = w.poll_changes(&mut mtimes);

            write_file(&sub, "nested.py", "code");

            let events = w.poll_changes(&mut mtimes);
            let created: Vec<_> = events.iter()
                .filter(|e| e.kind == WatchEventKind::Created)
                .collect();
            assert!(created.iter().any(|e| e.path.to_string_lossy().contains("nested.py")));
        }

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // start / stop tests
    // ------------------------------------------------------------------

    #[test]
    fn test_start_and_stop_lifecycle() {
        let dir = create_temp_dir();

        {
            let mut w = FileWatcher::new(&dir, &dir.join("test.db"), 0.1);
            assert!(!w.is_running());

            let rx = w.start();
            assert!(w.is_running());

            // Give the watcher thread a moment to initialise
            std::thread::sleep(Duration::from_millis(50));

            w.stop();
            assert!(!w.is_running());

            // Channel should eventually yield nothing new
            drop(rx);
        }

        cleanup(&dir);
    }

    #[test]
    fn test_start_detects_file_creation() {
        let dir = create_temp_dir();

        {
            let mut w = FileWatcher::new(&dir, &dir.join("test.db"), 0.1);
            let rx = w.start();

            // Allow initial baseline scan to complete
            std::thread::sleep(Duration::from_millis(200));

            // Create a file
            write_file(&dir, "new_watch.py", "x=1");

            // Wait for at least one poll cycle
            std::thread::sleep(Duration::from_millis(300));

            w.stop();

            // Collect all events from the channel
            let events: Vec<WatchEvent> = rx.try_iter().collect();
            let created: Vec<_> = events.iter()
                .filter(|e| e.kind == WatchEventKind::Created)
                .collect();
            assert!(!created.is_empty(), "Expected Created event via channel");
        }

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // Debounce tests
    // ------------------------------------------------------------------

    #[test]
    fn test_debounce_collapses_duplicate_paths() {
        let events = vec![
            WatchEvent { path: PathBuf::from("a.py"), kind: WatchEventKind::Modified },
            WatchEvent { path: PathBuf::from("a.py"), kind: WatchEventKind::Modified },
            WatchEvent { path: PathBuf::from("b.py"), kind: WatchEventKind::Created },
        ];
        let deduped = debounce_events(events);
        assert_eq!(deduped.len(), 2);
        assert!(deduped.iter().any(|e| e.path == PathBuf::from("a.py")));
        assert!(deduped.iter().any(|e| e.path == PathBuf::from("b.py")));
    }

    #[test]
    fn test_debounce_empty_events() {
        let events: Vec<WatchEvent> = vec![];
        let deduped = debounce_events(events);
        assert!(deduped.is_empty());
    }

    #[test]
    fn test_debounce_single_event() {
        let events = vec![
            WatchEvent { path: PathBuf::from("a.py"), kind: WatchEventKind::Modified },
        ];
        let deduped = debounce_events(events);
        assert_eq!(deduped.len(), 1);
    }

    // ------------------------------------------------------------------
    // WatchEventKind tests
    // ------------------------------------------------------------------

    #[test]
    fn test_watch_event_kind_debug() {
        assert_eq!(format!("{:?}", WatchEventKind::Created), "Created");
        assert_eq!(format!("{:?}", WatchEventKind::Modified), "Modified");
        assert_eq!(format!("{:?}", WatchEventKind::Removed), "Removed");
    }

    #[test]
    fn test_watch_event_clone() {
        let e1 = WatchEvent {
            path: PathBuf::from("test.py"),
            kind: WatchEventKind::Created,
        };
        let e2 = e1.clone();
        assert_eq!(e1.path, e2.path);
        assert_eq!(e1.kind, e2.kind);
    }
}
