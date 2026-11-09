// Lane P3: LD_PRELOAD shim that logs libc calls with CLOCK_MONOTONIC times (us since the first logged call of the
// image), their duration and the minor faults so far, to fd 2 (or $CALLTRACE_FD). Not for rustix raw syscalls.
// build: gcc -O2 -shared -fPIC -o calltrace.so calltrace.c -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <dirent.h>
#include <time.h>
#include <unistd.h>

static double t0 = -1;
static double now(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}
static long flt(void) {
  struct rusage ru;
  getrusage(RUSAGE_SELF, &ru);
  return ru.ru_minflt;
}
static void logf_(double start, const char *fmt, ...) {
  double end = now();
  if (t0 < 0) t0 = start;
  char buf[512];
  int n = snprintf(buf, sizeof buf, "ct %d t=%8.0f dur=%6.0f flt=%5ld ", (int)gettid(), start - t0,
                   end - start, flt());
  va_list ap;
  va_start(ap, fmt);
  n += vsnprintf(buf + n, sizeof buf - n, fmt, ap);
  va_end(ap);
  if (n > (int)sizeof buf - 2) n = sizeof buf - 2;
  buf[n++] = '\n';
  static ssize_t (*w)(int, const void *, size_t);
  if (!w) w = dlsym(RTLD_NEXT, "write");
  w(2, buf, n);
}
__attribute__((constructor)) static void init(void) { double s = now(); logf_(s, "ctor"); }
#define REAL(name) static __typeof__(name) *real_##name; if (!real_##name) real_##name = dlsym(RTLD_NEXT, #name)

int pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*f)(void *), void *arg) {
  REAL(pthread_create); double s = now(); int r = real_pthread_create(t, a, f, arg);
  size_t ss = 0; if (a) pthread_attr_getstacksize(a, &ss);
  logf_(s, "pthread_create stack=%zu r=%d", ss, r); return r;
}
void *mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
  REAL(mmap); double s = now(); void *r = real_mmap(addr, len, prot, flags, fd, off);
  logf_(s, "mmap len=%zu prot=%d flags=0x%x fd=%d", len, prot, flags, fd); return r;
}
int munmap(void *addr, size_t len) {
  REAL(munmap); double s = now(); int r = real_munmap(addr, len); logf_(s, "munmap len=%zu", len); return r;
}
int madvise(void *addr, size_t len, int adv) {
  REAL(madvise); double s = now(); int r = real_madvise(addr, len, adv); logf_(s, "madvise len=%zu adv=%d", len, adv); return r;
}
int mprotect(void *addr, size_t len, int prot) {
  REAL(mprotect); double s = now(); int r = real_mprotect(addr, len, prot); logf_(s, "mprotect len=%zu prot=%d", len, prot); return r;
}
int open64(const char *p, int fl, ...) {
  REAL(open64); va_list ap; va_start(ap, fl); int m = va_arg(ap, int); va_end(ap);
  double s = now(); int r = real_open64(p, fl, m); logf_(s, "open %s r=%d", p, r); return r;
}
int open(const char *p, int fl, ...) {
  REAL(open); va_list ap; va_start(ap, fl); int m = va_arg(ap, int); va_end(ap);
  double s = now(); int r = real_open(p, fl, m); logf_(s, "open %s r=%d", p, r); return r;
}
int openat64(int d, const char *p, int fl, ...) {
  REAL(openat64); va_list ap; va_start(ap, fl); int m = va_arg(ap, int); va_end(ap);
  double s = now(); int r = real_openat64(d, p, fl, m); logf_(s, "openat %s r=%d", p, r); return r;
}
int openat(int d, const char *p, int fl, ...) {
  REAL(openat); va_list ap; va_start(ap, fl); int m = va_arg(ap, int); va_end(ap);
  double s = now(); int r = real_openat(d, p, fl, m); logf_(s, "openat %s r=%d", p, r); return r;
}
ssize_t read(int fd, void *b, size_t n) {
  REAL(read); double s = now(); ssize_t r = real_read(fd, b, n); logf_(s, "read fd=%d n=%zu r=%zd", fd, n, r); return r;
}
ssize_t write(int fd, const void *b, size_t n) {
  REAL(write); double s = now(); ssize_t r = real_write(fd, b, n); logf_(s, "write fd=%d n=%zu", fd, n); return r;
}
int posix_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *fa, const posix_spawnattr_t *at,
                char *const argv[], char *const envp[]) {
  REAL(posix_spawn); double s = now(); int r = real_posix_spawn(pid, path, fa, at, argv, envp);
  logf_(s, "posix_spawn %s", path); return r;
}
int posix_spawnp(pid_t *pid, const char *path, const posix_spawn_file_actions_t *fa, const posix_spawnattr_t *at,
                 char *const argv[], char *const envp[]) {
  REAL(posix_spawnp); double s = now(); int r = real_posix_spawnp(pid, path, fa, at, argv, envp);
  logf_(s, "posix_spawnp %s", path); return r;
}
int execve(const char *p, char *const a[], char *const e[]) {
  REAL(execve); double s = now(); logf_(s, "execve %s", p); return real_execve(p, a, e);
}
int execvp(const char *p, char *const a[]) {
  REAL(execvp); double s = now(); logf_(s, "execvp %s", p); return real_execvp(p, a);
}
void exit(int c) { REAL(exit); double s = now(); logf_(s, "exit %d", c); real_exit(c); __builtin_unreachable(); }
void _exit(int c) { REAL(_exit); double s = now(); logf_(s, "_exit %d", c); real__exit(c); __builtin_unreachable(); }
int sched_getaffinity(pid_t p, size_t n, cpu_set_t *m) {
  REAL(sched_getaffinity); double s = now(); int r = real_sched_getaffinity(p, n, m); logf_(s, "sched_getaffinity"); return r;
}
int statx(int d, const char *p, int fl, unsigned int mask, struct statx *b) {
  REAL(statx); double s = now(); int r = real_statx(d, p, fl, mask, b); logf_(s, "statx %s r=%d", p, r); return r;
}
int stat64(const char *p, struct stat64 *b) {
  REAL(stat64); double s = now(); int r = real_stat64(p, b); logf_(s, "stat %s r=%d", p, r); return r;
}
int lstat64(const char *p, struct stat64 *b) {
  REAL(lstat64); double s = now(); int r = real_lstat64(p, b); logf_(s, "lstat %s r=%d", p, r); return r;
}
int fstatat64(int d, const char *p, struct stat64 *b, int fl) {
  REAL(fstatat64); double s = now(); int r = real_fstatat64(d, p, b, fl); logf_(s, "fstatat %s r=%d", p, r); return r;
}
ssize_t readlink(const char *p, char *b, size_t n) {
  REAL(readlink); double s = now(); ssize_t r = real_readlink(p, b, n); logf_(s, "readlink %s r=%zd", p, r); return r;
}
struct dirent64 *readdir64(DIR *d) {
  REAL(readdir64); double s = now(); struct dirent64 *r = real_readdir64(d); logf_(s, "readdir"); return r;
}
