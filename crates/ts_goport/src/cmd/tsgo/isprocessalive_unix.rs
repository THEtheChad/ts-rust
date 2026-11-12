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
// truncated only there (`kill_target`).
pub fn is_process_alive(pid: i64) -> bool {
    let result = match kill_target(pid) {
        KillTarget::None => return false,
        KillTarget::CurrentGroup => test_kill_current_process_group(),
        KillTarget::Process(raw) => test_kill_process(raw),
        KillTarget::Group(raw) => test_kill_process_group(raw),
    };
    matches!(result, Ok(()) | Err(Errno::PERM))
}

/// What `kill(pid_t(pid), 0)` signals.
#[derive(Debug, PartialEq, Eq)]
enum KillTarget {
    /// `kill(INT_MIN, 0)`: the kernel refuses `-INT_MIN` with ESRCH.
    None,
    /// `kill(0, 0)`: the caller's process group.
    CurrentGroup,
    /// `kill(p, 0)` with `p > 0`: that process.
    Process(Pid),
    /// `kill(-g, 0)`: process group `g`. `g` 1 is `kill(-1, 0)`, every
    /// process the caller may signal: ESRCH when there is none (an empty
    /// PID namespace), and Go's watchdog then shuts the server down.
    Group(Pid),
}

/// The 32-bit `pid_t` of Go's `int` pid: 2^31 is `kill(INT_MIN, 0)`, 2^32
/// is `kill(0, 0)`, 2^32 + 1 is init and 2^32 - 1 is `kill(-1, 0)`.
fn kill_target(pid: i64) -> KillTarget {
    let pid_t = pid as i32;
    match pid_t {
        i32::MIN => KillTarget::None,
        0 => KillTarget::CurrentGroup,
        _ => {
            let raw = Pid::from_raw(pid_t.abs()).expect("a pid_t other than 0 and INT_MIN");
            if pid_t > 0 {
                KillTarget::Process(raw)
            } else {
                KillTarget::Group(raw)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{KillTarget, Pid, is_process_alive, kill_target};

    fn pid(raw: i32) -> Pid {
        Pid::from_raw(raw).expect("a positive pid")
    }

    // Lane B9 (audit A7, Astra round 1): the pid is a Go `int` until
    // `kill`, which reads a 32-bit `pid_t`; -1 is `kill(-1, 0)`, checked
    // like any group, not assumed alive.
    #[test]
    fn kill_target_truncates_to_pid_t() {
        assert_eq!(kill_target(1 << 31), KillTarget::None);
        assert_eq!(kill_target(1 << 32), KillTarget::CurrentGroup);
        assert_eq!(kill_target((1 << 32) + 1), KillTarget::Process(pid(1)));
        assert_eq!(kill_target(0xFFFF_FFFF), KillTarget::Group(pid(1)));
        assert_eq!(kill_target(-1), KillTarget::Group(pid(1)));
        assert_eq!(kill_target(-5), KillTarget::Group(pid(5)));
        assert_eq!(
            kill_target(0x7FFF_FFFF),
            KillTarget::Process(pid(0x7FFF_FFFF))
        );
    }

    // 2^31 is `kill(INT_MIN, 0)` (ESRCH: not alive, so Go's watchdog shuts
    // the server down); 2^32 is `kill(0, 0)` (the caller's group: alive);
    // 2^32 plus this process's pid is this process.
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
