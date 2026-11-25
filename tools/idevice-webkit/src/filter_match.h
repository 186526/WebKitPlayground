#ifndef IDEVICE_WEBKIT_FILTER_MATCH_H
#define IDEVICE_WEBKIT_FILTER_MATCH_H

#include "types.h"

/* Which branch of the ellekit filter let this process load the dylib. The
   filter file holds one key, Filter, whose value is the dictionary with the
   Classes and the Bundles array; libinjector loads the dylib when any element
   of either array matches, so the answer is read the same way libinjector
   reads it (see any_element()) and then written into the report. A filter
   without a Filter key is read as the older flat shape, where the two arrays
   are the file dictionary's own keys. A process neither branch names keeps
   match_none in a report of its own: the match is written for every process,
   because a filter this dylib could not read must not stop the host work of a
   process ellekit did inject.

   The answer is also returned, because the image claim is gated on it: a
   process no branch of the filter names has no proof that ellekit meant to
   load this dylib into it, and the claim maps seven images, so it stays off.
   false covers both «no branch matched» and «the filter could not be read»,
   and both cases leave the rest of the constructor exactly as it was. */

bool filter_match_report(const char *filterPath);

#endif
