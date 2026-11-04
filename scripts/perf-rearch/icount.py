#!/usr/bin/env python3
# Counts the user-space instructions of a command and its children (perf_event_open, excl_kernel; works at
# perf_event_paranoid=2 without perf or sudo). Load-independent: repeat runs agree within about 0.005%.
# usage: GOPORT_LAUNCH=0 python3 -I scripts/perf-rearch/icount.py <bin> [args...]
# Use --singleThreaded for checker claims; multi-threaded counts include waits that depend on timing.
import ctypes, os, struct, sys
if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
    print(__doc__ or "usage: icount.py <command> [args...]"); sys.exit(0)
libc = ctypes.CDLL(None, use_errno=True)
attr = bytearray(128)
struct.pack_into('IIQ', attr, 0, 0, 128, 1)  # PERF_TYPE_HARDWARE, size, PERF_COUNT_HW_INSTRUCTIONS
struct.pack_into('Q', attr, 40, (1<<0)|(1<<1)|(1<<5)|(1<<6)|(1<<12))  # disabled|inherit|excl_kernel|excl_hv|enable_on_exec
r, w = os.pipe(); pid = os.fork()
if pid == 0:
    os.close(w); os.read(r, 1); os.execvp(sys.argv[1], sys.argv[1:])
fd = libc.syscall(298, (ctypes.c_char*128).from_buffer(attr), pid, -1, -1, 0)
if fd < 0:
    print("perf_event_open failed", ctypes.get_errno(), file=sys.stderr); os.kill(pid, 9); sys.exit(1)
os.write(w, b'x'); _, status = os.waitpid(pid, 0)
print("user instructions:", struct.unpack('Q', os.read(fd, 8))[0], file=sys.stderr)
sys.exit(os.waitstatus_to_exitcode(status))
