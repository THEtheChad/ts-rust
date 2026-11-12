//! Go `cmd/tsc/isprocessalive_unix.go` (`//go:build unix`; the module is
//! declared under `#[cfg(unix)]`).

use rustix::io::Errno;
use rustix::process::{
    Pid, test_kill_current_process_group, test_kill_process, test_kill_process_group,
};

// Go: cmd/tsc/isprocessalive_unix.go:11 processAliveSupported
pub const PROCESS_ALIVE_SUPPORTED: bool = true;

// Go: cmd/tsc/isprocessalive_unix.go:18 isProcessAlive
// isProcessAlive checks if a process with the given PID is still running.
// On Unix, FindProcess always succeeds, so we send signal 0 to probe the
// process. If the signal returns nil or EPERM, the process exists (EPERM
// means it exists but we lack permission to signal it). ESRCH or any
// other error indicates the process is gone.
// PORT: `proc.Signal(syscall.Signal(0))` is `kill(pid, 0)`
// (`test_kill_process`). On Linux Go signals through a pidfd; for a live
// pid both give the same result. The only caller passes a pid above 0 (a Go
// `int`). The kernel reads it as a 32-bit `pid_t`, so Go's value is
// truncated only there: a pid of 2^31 is `kill(INT_MIN, 0)` (ESRCH), 2^32
// is `kill(0, 0)` (the caller's process group) and 2^32 + 1 is init. A
// `pid_t` of -1 signals every process the user may signal; that includes
// the client that started the server, so it counts as alive.
pub fn is_process_alive(pid: i64) -> bool {
    let pid_t = pid as i32;
    let result = match pid_t {
        i32::MIN => return false,
        -1 => return true,
        0 => test_kill_current_process_group(),
        _ => match Pid::from_raw(pid_t.saturating_abs()) {
            Some(raw) if pid_t > 0 => test_kill_process(raw),
            Some(raw) => test_kill_process_group(raw),
            None => return false,
        },
    };
    matches!(result, Ok(()) | Err(Errno::PERM))
}

#[cfg(test)]
mod tests {
    use super::is_process_alive;

    // Lane B9 (audit A7): the pid is a Go `int` until `kill`, which reads a
    // 32-bit `pid_t`. 2^31 is `kill(INT_MIN, 0)` (ESRCH: not alive, so
    // Go's watchdog shuts the server down); 2^32 is `kill(0, 0)` (the
    // caller's group: alive); 2^32 plus this process's pid is this process.
    #[test]
    fn pid_is_truncated_only_at_kill() {
        let own = i64::from(std::process::id());
        assert!(is_process_alive(own));
        assert!(is_process_alive((1 << 32) + own));
        assert!(is_process_alive(1 << 32));
        assert!(!is_process_alive(1 << 31));
        assert!(!is_process_alive(0x7FFF_FFFF));
    }
}
