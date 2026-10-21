#include "qpv/parse_double.hpp"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <locale.h>
#include <string>
#include <string_view>
#include <version>
#ifdef __APPLE__
#include <xlocale.h>
#endif

namespace qpv {
namespace {

bool is_digit(char c) { return c >= '0' && c <= '9'; }
bool is_alpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
char lower(char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a') : c; }

// True when [p, end) starts with `word` (lower case), ignoring case.
bool starts_with(const char* p, const char* end, std::string_view word) {
    if (static_cast<std::size_t>(end - p) < word.size()) return false;
    for (const char c : word)
        if (lower(*p++) != c) return false;
    return true;
}

// The end of the longest prefix of [begin, end) that std::from_chars reads in
// its general format, or `begin` when there is none. No leading '+', spaces
// or hexadecimal, so "0x1" is the number 0 followed by "x1".
const char* number_end(const char* begin, const char* end) {
    const char* p = begin;
    if (p != end && *p == '-') ++p;
    if (starts_with(p, end, "inf")) return p + (starts_with(p, end, "infinity") ? 8 : 3);
    if (starts_with(p, end, "nan")) {
        p += 3;
        if (p != end && *p == '(') {
            const char* q = p + 1;
            while (q != end && (is_digit(*q) || is_alpha(*q) || *q == '_')) ++q;
            if (q != end && *q == ')') return q + 1;
        }
        return p;
    }
    const char* mantissa = p;
    while (p != end && is_digit(*p)) ++p;
    bool digits = p != mantissa;
    if (p != end && *p == '.') {
        const char* fraction = ++p;
        while (p != end && is_digit(*p)) ++p;
        digits = digits || p != fraction;
    }
    if (!digits) return begin;
    if (p != end && (*p == 'e' || *p == 'E')) {
        const char* q = p + 1;
        if (q != end && (*q == '+' || *q == '-')) ++q;
        if (q != end && is_digit(*q)) {
            while (q != end && is_digit(*q)) ++q;
            p = q;
        }
    }
    return p;
}

// strtod in the "C" locale, whatever the current one is.
double strtod_c(const char* text, char** stop) {
#ifdef _WIN32
    static const _locale_t c_locale = _create_locale(LC_ALL, "C");
    return _strtod_l(text, stop, c_locale);
#else
    static const locale_t c_locale = newlocale(LC_ALL_MASK, "C", locale_t{});
    return strtod_l(text, stop, c_locale);
#endif
}

} // namespace

namespace detail {

std::from_chars_result parse_double_fallback(const char* begin, const char* end,
                                             double& value) {
    const char* stop = number_end(begin, end);
    if (stop == begin) return {begin, std::errc::invalid_argument};
    // Not left to strtod: the older msvcrt.dll does not read these.
    const char* unsigned_begin = begin + (*begin == '-' ? 1 : 0);
    if (!is_digit(*unsigned_begin) && *unsigned_begin != '.') {
        const double special = lower(*unsigned_begin) == 'i'
                                   ? std::numeric_limits<double>::infinity()
                                   : std::numeric_limits<double>::quiet_NaN();
        value = *begin == '-' ? -special : special;
        return {stop, std::errc{}};
    }
    // strtod_l needs a terminated string and must not read past `end`.
    const std::string text(begin, stop);
    char* text_end = nullptr;
    const int saved_errno = errno;
    errno = 0;
    const double result = strtod_c(text.c_str(), &text_end);
    const bool range_error = errno == ERANGE;
    errno = saved_errno;
    if (text_end != text.c_str() + text.size()) return {begin, std::errc::invalid_argument};
    // Overflow, or underflow to zero; subnormal results are kept.
    if (range_error && (std::isinf(result) || result == 0.0))
        return {stop, std::errc::result_out_of_range};
    value = result;
    return {stop, std::errc{}};
}

} // namespace detail

std::from_chars_result parse_double(const char* begin, const char* end, double& value) {
    const char* start = begin;
    if (start != end && *start == '+' && (end - start < 2 || start[1] != '-')) ++start;
#if defined(__cpp_lib_to_chars) && __cpp_lib_to_chars >= 201611L
    auto result = std::from_chars(start, end, value);
#else
    auto result = detail::parse_double_fallback(start, end, value);
#endif
    if (result.ec == std::errc::invalid_argument) result.ptr = begin;
    return result;
}

} // namespace qpv
