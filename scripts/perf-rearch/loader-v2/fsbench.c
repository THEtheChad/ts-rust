// fsbench: replay the OS-level calls of one load, time them per kind: wall, and user and
// system CPU apart (getrusage(RUSAGE_THREAD) per thread).
//
// usage: fsbench calls.tsv [threads] [reps]
//   threads=1: per-kind wall, utime and stime (each kind on its own, in trace order).
//   threads>1: all calls split in contiguous ranges over the threads; wall, and utime and stime
//              summed over the threads.
//   reps (default 3): the last rep is printed (the earlier ones warm the dentry cache).
// build: gcc -O2 -Wall -o fsbench fsbench.c -lpthread
//
// Input: the real backend-call trace of a load, `kind<TAB>path[<TAB>who]` per line, from
// GOPORT_LOAD_VIEW_TRACE=<file> (crates/ts_goport/src/frontend/vfs/loadview.rs): every OS call
// the per-load view made (loader misses and worker fills), with the exact path strings. Kinds:
// stat, read, realpath, entries. Each kind replays the syscalls that tsc-rs makes for it:
//   stat      statx(AT_FDCWD, path, 0, STATX_BASIC_STATS)      (std::fs::metadata)
//   read      open + statx(fd) + read to EOF + close           (std::fs::read)
//   realpath  open(O_PATH) + readlink(/proc/self/fd/N) + close (frontend/nativepath.rs)
//   entries   open(O_DIRECTORY) + getdents64 + statx (follow) of each symlink + close
//             (vfs Common::get_accessible_entries over osvfs read_dir_entries)
// The old kinds file_exists and directory_exists are read as stat.
//
// Fixes over the first version (Astra's loader-v2 review, finding 8): each thread has its own
// sink (no data race), the input is the call trace (not the snapshot cache contents, 96 of whose
// read paths were not absolute), and user and system CPU are measured apart.
#define _GNU_SOURCE
#include <dirent.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

enum { STAT, READ, REALPATH, ENTRIES, KINDS };
static const char *names[KINDS] = {"stat(statx)", "read(open+statx+read+close)",
                                   "realpath(open O_PATH+readlink+close)", "entries(getdents+statx links)"};
typedef struct { int kind; char *path; } call;
static call *calls;
static int n;

static double now_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
static double tv_ms(struct timeval t) { return t.tv_sec * 1e3 + t.tv_usec / 1e3; }

typedef struct { double user, sys; } cpu;
static cpu thread_cpu(void) {
  struct rusage r;
  getrusage(RUSAGE_THREAD, &r);
  return (cpu){tv_ms(r.ru_utime), tv_ms(r.ru_stime)};
}

struct linux_dirent64 { unsigned long long d_ino; long long d_off; unsigned short d_reclen; unsigned char d_type; char d_name[]; };

// One call; `sink` and `buf` are the calling thread's own.
static void do_call(const call *c, char *buf, size_t cap, long *sink) {
  struct statx sx;
  switch (c->kind) {
  case STAT:
    *sink += statx(AT_FDCWD, c->path, 0, STATX_BASIC_STATS, &sx);
    break;
  case READ: {
    int fd = open(c->path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) { (*sink)--; break; }
    *sink += statx(fd, "", AT_EMPTY_PATH, STATX_BASIC_STATS, &sx);
    ssize_t r;
    while ((r = read(fd, buf, cap)) > 0) *sink += r;
    close(fd);
    break;
  }
  case REALPATH: {
    int fd = open(c->path, O_PATH | O_CLOEXEC);
    if (fd < 0) { (*sink)--; break; }
    char p[64], out[4096];
    snprintf(p, sizeof p, "/proc/self/fd/%d", fd);
    *sink += readlink(p, out, sizeof out);
    close(fd);
    break;
  }
  case ENTRIES: {
    int fd = open(c->path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) { (*sink)--; break; }
    char d[8192];
    long got;
    while ((got = syscall(SYS_getdents64, fd, d, sizeof d)) > 0) {
      for (long off = 0; off < got;) {
        struct linux_dirent64 *e = (struct linux_dirent64 *)(d + off);
        off += e->d_reclen;
        (*sink)++;
        if (e->d_type == DT_LNK || e->d_type == DT_UNKNOWN) *sink += statx(fd, e->d_name, 0, STATX_BASIC_STATS, &sx);
      }
    }
    close(fd);
    break;
  }
  }
}

typedef struct { int lo, hi, kind; long sink; cpu used; } range;
static void *worker(void *a) {
  range *r = a;
  size_t cap = 1 << 20;
  char *buf = malloc(cap);
  cpu t0 = thread_cpu();
  for (int i = r->lo; i < r->hi; i++)
    if (r->kind < 0 || calls[i].kind == r->kind) do_call(&calls[i], buf, cap, &r->sink);
  cpu t1 = thread_cpu();
  r->used = (cpu){t1.user - t0.user, t1.sys - t0.sys};
  free(buf);
  return 0;
}

int main(int argc, char **argv) {
  if (argc < 2) { fprintf(stderr, "usage: fsbench calls.tsv [threads] [reps]\n"); return 2; }
  FILE *f = fopen(argv[1], "r");
  if (!f) { perror(argv[1]); return 2; }
  int threads = argc > 2 ? atoi(argv[2]) : 1, reps = argc > 3 ? atoi(argv[3]) : 3;
  if (threads < 1 || threads > 64) threads = 1;
  int cap = 1 << 16, count[KINDS] = {0}, skipped = 0;
  calls = malloc(sizeof(call) * cap);
  char *line = NULL;
  size_t len = 0;
  while (getline(&line, &len, f) > 0) {
    line[strcspn(line, "\n")] = 0;
    char *t = strchr(line, '\t');
    if (!t) continue;
    *t = 0;
    char *p = t + 1, *who = strchr(p, '\t');
    if (who) *who = 0;
    int k = !strcmp(line, "stat") || !strcmp(line, "file_exists") || !strcmp(line, "directory_exists") ? STAT
            : !strcmp(line, "read") ? READ : !strcmp(line, "realpath") ? REALPATH : !strcmp(line, "entries") ? ENTRIES : -1;
    if (k < 0 || p[0] != '/') { skipped++; continue; }
    if (n == cap) calls = realloc(calls, sizeof(call) * (cap *= 2));
    calls[n].kind = k;
    calls[n].path = strdup(p);
    count[k]++;
    n++;
  }
  printf("calls %d (stat %d, read %d, realpath %d, entries %d), skipped %d\n", n, count[STAT], count[READ],
         count[REALPATH], count[ENTRIES], skipped);
  long total_sink = 0;
  for (int rep = 0; rep < reps; rep++) {
    int last = rep == reps - 1;
    if (threads == 1) {
      double wall = 0, user = 0, sys = 0;
      for (int k = 0; k < KINDS; k++) {
        range r = {0, n, k, 0, {0, 0}};
        double t0 = now_ms();
        worker(&r);
        double dt = now_ms() - t0;
        wall += dt, user += r.used.user, sys += r.used.sys, total_sink += r.sink;
        if (last && count[k])
          printf("%-40s n=%6d wall %8.2f ms (%5.2f us/call)  utime %7.2f  stime %7.2f ms\n", names[k], count[k], dt,
                 dt * 1e3 / count[k], r.used.user, r.used.sys);
      }
      if (last) printf("%-40s          wall %8.2f ms                  utime %7.2f  stime %7.2f ms\n", "total", wall, user, sys);
    } else {
      pthread_t th[64];
      range r[64];
      double t0 = now_ms();
      for (int i = 0; i < threads; i++) {
        r[i] = (range){(int)((long)n * i / threads), (int)((long)n * (i + 1) / threads), -1, 0, {0, 0}};
        pthread_create(&th[i], 0, worker, &r[i]);
      }
      double user = 0, sys = 0;
      for (int i = 0; i < threads; i++) {
        pthread_join(th[i], 0);
        user += r[i].used.user, sys += r[i].used.sys, total_sink += r[i].sink;
      }
      if (last) printf("all %d calls on %d threads: wall %.2f ms, utime %.2f ms, stime %.2f ms (summed)\n", n, threads,
                       now_ms() - t0, user, sys);
    }
  }
  return total_sink == 12345;
}
