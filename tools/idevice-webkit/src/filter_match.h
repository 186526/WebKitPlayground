#ifndef IDEVICE_WEBKIT_FILTER_MATCH_H
#define IDEVICE_WEBKIT_FILTER_MATCH_H

#include "types.h"

/* Which branch of the ellekit filter let this process load the dylib. The
   filter names a Classes array and a Bundles array, and libinjector loads the
   dylib when the first element of either one matches, so the answer is read
   the same way libinjector reads it and then written into the report. A
   process neither branch names keeps match_none in a report of its own: the
   match is written for every process, because a filter this dylib could not
   read must not stop the host work of a process ellekit did inject. */

void filter_match_report(const char *filterPath);

#endif
