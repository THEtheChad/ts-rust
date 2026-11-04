// fsbench: replay the OS-level lookups of one load (kind \t path), time per kind.
// usage: fsbench calls.tsv [threads]   (threads=1 prints per-kind times)
// build: gcc -O2 -o fsbench fsbench.c -lpthread; input: TSV of kind<TAB>path, kinds file_exists|directory_exists|realpath|read|entries
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <dirent.h>
#include <time.h>
#include <pthread.h>
typedef struct { int kind; char *path; } call;
static call *calls; static int n;
static double now(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e3+t.tv_nsec/1e6;}
static char buf[1<<22];
static long sink;
static void do_call(call *c, char *b){
  struct stat st; char p[64], out[4096];
  switch(c->kind){
  case 0: case 1: sink += stat(c->path,&st); break;            // file_exists, directory_exists
  case 2: { int fd=open(c->path,O_PATH|O_CLOEXEC); if(fd>=0){snprintf(p,64,"/proc/self/fd/%d",fd); sink+=readlink(p,out,4096); close(fd);} break; }
  case 3: { int fd=open(c->path,O_RDONLY|O_CLOEXEC); if(fd>=0){fstat(fd,&st); long r; while((r=read(fd,b,1<<22))>0) sink+=r; close(fd);} break; }
  case 4: { DIR*d=opendir(c->path); if(d){struct dirent*e; while((e=readdir(d))){ if(e->d_type==DT_UNKNOWN||e->d_type==DT_LNK){fstatat(dirfd(d),e->d_name,&st,0);} sink++;} closedir(d);} break; }
  case 5: { struct statx sx; sink += statx(AT_FDCWD,c->path,0,STATX_TYPE|STATX_MODE,&sx); break; }
  }
}
typedef struct { int lo, hi; } range;
static void *worker(void *a){ range *r=a; char *b=malloc(1<<22); for(int i=r->lo;i<r->hi;i++) do_call(&calls[i],b); free(b); return 0; }
int main(int argc,char**argv){
  FILE*f=fopen(argv[1],"r"); int threads=argc>2?atoi(argv[2]):1; char line[8192];
  calls=malloc(sizeof(call)*200000);
  while(fgets(line,sizeof line,f)){ char*t=strchr(line,'\t'); if(!t)continue; *t=0; char*p=t+1; p[strcspn(p,"\n")]=0;
    int k=!strcmp(line,"file_exists")?0:!strcmp(line,"directory_exists")?1:!strcmp(line,"realpath")?2:!strcmp(line,"read")?3:!strcmp(line,"entries")?4:-1;
    if(k<0)continue; calls[n].kind=k; calls[n].path=strdup(p); n++; }
  const char*names[]={"file_exists(stat)","directory_exists(stat)","realpath(open+readlink+close)","read(open+fstat+read+close)","readdir","statx-only"};
  for(int rep=0;rep<3;rep++){
    if(threads==1){
      double tot=0;
      for(int k=0;k<5;k++){ double t0=now(); int c=0; for(int i=0;i<n;i++) if(calls[i].kind==k){do_call(&calls[i],buf);c++;} double dt=now()-t0; tot+=dt;
        if(rep==2) printf("%-32s n=%6d  %7.2f ms  %5.2f us/call\n",names[k],c,dt,dt*1e3/c); }
      if(rep==2) printf("%-32s          %7.2f ms\n","total",tot);
    } else {
      pthread_t th[64]; range r[64]; double t0=now();
      for(int i=0;i<threads;i++){ r[i].lo=(long)n*i/threads; r[i].hi=(long)n*(i+1)/threads; pthread_create(&th[i],0,worker,&r[i]); }
      for(int i=0;i<threads;i++) pthread_join(th[i],0);
      if(rep==2) printf("all %d calls on %d threads: %.2f ms wall\n",n,threads,now()-t0);
    }
  }
  return sink==12345;
}
