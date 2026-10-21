#pragma once

// The licensing switch. With 0 (the default) the programs are unrestricted
// and never look for a license. With 1, conversions, Thumbnail Mode, Assign
// Keywords and keyword editing need a license key once a 30-day trial ends;
// viewing stays free.
//
// Change the 0 below, or configure CMake with -DQPV_LICENSING=ON, which
// defines it for every target. Before turning it on, make the signing key with
// qpv-keygen (see README.md, "Licensing"), or the build stops with an error.
#ifndef QPV_LICENSING
#define QPV_LICENSING 0
#endif

// Length of the trial, counted from the first run under each Windows user.
#ifndef QPV_TRIAL_DAYS
#define QPV_TRIAL_DAYS 30
#endif

// Where customers can buy a license. When not empty, the viewer shows it as a
// link in the license dialog and in the trial-ended message.
#ifndef QPV_PURCHASE_URL
#define QPV_PURCHASE_URL ""
#endif
