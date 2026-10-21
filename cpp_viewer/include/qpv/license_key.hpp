#pragma once

// License keys: an Ed25519-style signature (Monocypher's EdDSA) over the
// customer's name or email and the date the key was issued. Only the holder of
// the private signing key can make one; the programs check them with the
// public key alone.

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace qpv::license {

using PublicKey = std::array<std::uint8_t, 32>;
// The private signing key is kept as the 32-byte seed it is derived from.
using SigningSeed = std::array<std::uint8_t, 32>;

struct KeyInfo {
    std::string licensee;  // normalised, as signed
    int issued_day = 0;    // days since 1970-01-01
    std::uint8_t flags = 0;
};

[[nodiscard]] PublicKey public_key_for(const SigningSeed& seed);

// A key such as "QPV1-XXXXX-XXXXX-…" for `licensee`. Throws
// std::invalid_argument for an empty name or a date it cannot hold.
[[nodiscard]] std::string issue_key(const SigningSeed& seed, std::string_view licensee,
                                    int issued_day, std::uint8_t flags = 0);

// The key's details when `key` was issued for `licensee` with the private half
// of `public_key`; otherwise nothing, with the reason in `error`. Case,
// spaces, dashes and line breaks in the key do not matter, nor case and spacing
// in the name.
[[nodiscard]] std::optional<KeyInfo> check_key(std::string_view key, std::string_view licensee,
                                               const PublicKey& public_key,
                                               std::string* error = nullptr);

// The name as shown: trimmed, with runs of white space made one space.
[[nodiscard]] std::string display_licensee(std::string_view licensee);
// The name as signed: display_licensee() with ASCII letters in lower case.
[[nodiscard]] std::string normalize_licensee(std::string_view licensee);

[[nodiscard]] int today();  // days since 1970-01-01, UTC
[[nodiscard]] std::string format_day(int day);  // YYYY-MM-DD
[[nodiscard]] std::optional<int> parse_day(std::string_view text);

[[nodiscard]] std::string to_hex(const std::uint8_t* data, std::size_t size);
[[nodiscard]] bool from_hex(std::string_view text, std::uint8_t* out, std::size_t size);

} // namespace qpv::license
