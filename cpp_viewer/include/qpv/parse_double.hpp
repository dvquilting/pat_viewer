#pragma once

#include <charconv>

namespace qpv {

// Reads a number from [begin, end) like std::from_chars in its general format,
// so the C locale's decimal separator is ignored, unlike strtod or std::stod.
// A leading '+' is also accepted, but not "+-". On success `ptr` is just past
// the number; on failure it is `begin` and `value` is unchanged.
[[nodiscard]] std::from_chars_result parse_double(const char* begin, const char* end,
                                                  double& value);

namespace detail {
// What parse_double uses where the standard library has no floating-point
// std::from_chars (Apple's libc++, for one). Like std::from_chars, it takes no
// leading '+'. Built everywhere so that the tests cover it.
[[nodiscard]] std::from_chars_result parse_double_fallback(const char* begin,
                                                           const char* end, double& value);
} // namespace detail

} // namespace qpv
