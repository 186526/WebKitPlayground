#ifndef IDEVICE_WEBKIT_STAGE_CLOCK_H
#define IDEVICE_WEBKIT_STAGE_CLOCK_H

#include "types.h"

/* How far the constructor got. Each value is written into the report as the
   stage an unanswered question of «did it run at all» is read from. The entry
   stage is written before the dylib judges its own path, so a process that
   loaded anything other than the installed copy still leaves its own path
   behind. */

#define stage_entered 1u
#define stage_not_installed_copy 2u
#define stage_bundle_resolved 3u
#define stage_attempted 4u
#define stage_report_written 5u
#define stage_holding 6u

void stage_reach(u32 stage);
u32 stage_current(void);
void stage_reset(void);

#endif
