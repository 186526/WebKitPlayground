#ifndef IDEVICE_WEBKIT_STAGE_CLOCK_H
#define IDEVICE_WEBKIT_STAGE_CLOCK_H

#include "types.h"

/* How far the constructor got. Each value is written into the report as the
   stage an unanswered question of «did it run at all» is read from. The entry
   stage is written before the dylib judges its own path, so a process that
   loaded anything other than the installed copy still leaves its own path
   behind. 1.0.3 reads the stage back, because ellekit loads the dylib into
   every process its filter matches, including the WebKit subprocesses that
   have no UIApplication: the report of those processes stops at the filter
   stage with matchVia set to none, while a main application reaches the
   stages above it. */

#define stage_entered 1u
#define stage_filter_read 2u
#define stage_not_installed_copy 3u
#define stage_bundle_resolved 4u
#define stage_attempted 5u
#define stage_report_written 6u
#define stage_holding 7u

void stage_reach(u32 stage);
u32 stage_current(void);
void stage_reset(void);

#endif
