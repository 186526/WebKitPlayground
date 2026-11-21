#include "stage_clock.h"

#include "dylib_report.h"
#include "types.h"

static u32 current_stage = 0;

void stage_reach(u32 stage) {
  current_stage = stage;
  dylib_report_set_stage(stage);
}

u32 stage_current(void) {
  return current_stage;
}

void stage_reset(void) {
  current_stage = 0;
}
