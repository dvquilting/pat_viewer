#pragma once

// The public half of the license signing key, which the programs use to check
// license keys. Replace this file with the one `qpv-keygen init` writes; the
// private half stays with you and is never compiled in.
//
// This placeholder cannot check any key, and a build with QPV_LICENSING on
// stops with an error while it is still here.
#define QPV_LICENSE_PUBLIC_KEY_PLACEHOLDER 1

#include <array>
#include <cstdint>

namespace qpv::license {

inline constexpr std::array<std::uint8_t, 32> embedded_public_key{};

} // namespace qpv::license
