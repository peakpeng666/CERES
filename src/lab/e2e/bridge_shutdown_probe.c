/* Diagnostic only. Widen the observed logging-shutdown race without skipping
 * any destruction, changing statuses or touching the real signal handler.
 * Stock main returns before SHUTDOWN_DONE; the fixed program must wait.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>
typedef int (*main_fn)(int, char **, char **);
typedef int (*start_fn)(main_fn, int, char **, void *, void *, void *, void *);
static main_fn original_main;
static int observed_main(int argc, char **argv, char **envp) {
  int status = original_main(argc, argv, envp);
  dprintf(2, "CERES_PROBE MAIN_RETURN %d\n", status);
  return status;
}
int __libc_start_main(main_fn main, int argc, char **argv, void *init,
                      void *fini, void *rtld_fini, void *stack_end) {
  original_main = main;
  start_fn start = (start_fn)dlsym(RTLD_NEXT, "__libc_start_main");
  return start(observed_main, argc, argv, init, fini, rtld_fini, stack_end);
}
int rcl_logging_external_shutdown(void) {
  int (*shutdown_logging)(void) = dlsym(RTLD_NEXT, "rcl_logging_external_shutdown");
  dprintf(2, "CERES_PROBE SHUTDOWN_BEGIN\n");
  usleep(500000);
  int status = shutdown_logging();
  dprintf(2, "CERES_PROBE SHUTDOWN_DONE %d\n", status);
  return status;
}
