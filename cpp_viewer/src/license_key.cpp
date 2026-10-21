#include "qpv/license_key.hpp"

#include "monocypher.h"

#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <vector>

namespace qpv::license {
namespace {

// Signed ahead of every key so a signature made for anything else with the
// same key pair can never pass as a license.
constexpr char signing_context[] = "QPV-LICENSE-v1";
constexpr std::uint8_t key_version = 1;
constexpr std::string_view key_prefix = "QPV1";
// The issue date is held in two bytes as days since 2020-01-01.
constexpr int day_base = 18262;
constexpr std::size_t payload_size = 4;  // version, flags, issue day (2)
constexpr std::size_t signature_size = 64;
constexpr std::size_t key_bytes = payload_size + signature_size;
constexpr std::size_t key_symbols = (key_bytes * 8 + 4) / 5;
constexpr std::size_t max_licensee_bytes = 200;
// Crockford's base32: no I, L, O or U, so keys read aloud or retyped survive.
constexpr char alphabet[] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

std::vector<std::uint8_t> signed_message(const std::uint8_t* payload, const std::string& licensee) {
    std::vector<std::uint8_t> message(signing_context, signing_context + sizeof signing_context);
    message.insert(message.end(), payload, payload + payload_size);
    message.insert(message.end(), licensee.begin(), licensee.end());
    return message;
}

std::string encode(const std::uint8_t* data, std::size_t size) {
    std::string symbols;
    unsigned buffer = 0;
    int bits = 0;
    for (std::size_t i = 0; i < size; ++i) {
        buffer = (buffer << 8) | data[i];
        bits += 8;
        while (bits >= 5) {
            bits -= 5;
            symbols += alphabet[(buffer >> bits) & 31];
        }
    }
    if (bits > 0) symbols += alphabet[(buffer << (5 - bits)) & 31];
    std::string text(key_prefix);
    for (std::size_t i = 0; i < symbols.size(); i += 5) text += '-' + symbols.substr(i, 5);
    return text;
}

int symbol_value(char c) {
    if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
    if (c == 'O') return 0;
    if (c == 'I' || c == 'L') return 1;
    for (int value = 0; value < 32; ++value)
        if (alphabet[value] == c) return value;
    return -1;
}

std::optional<std::vector<std::uint8_t>> decode(std::string_view text) {
    std::string symbols;
    for (char c : text)
        if (c != '-' && c != ' ' && c != '\t' && c != '\r' && c != '\n') symbols += c;
    // The prefix is only recognised by length, so a key pasted without it,
    // whose data might begin with the same letters, still reads correctly.
    if (symbols.size() == key_prefix.size() + key_symbols) {
        for (std::size_t i = 0; i < key_prefix.size(); ++i) {
            char c = symbols[i];
            if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
            if (c == 'I' || c == 'L') c = '1';
            if (c != key_prefix[i]) return std::nullopt;
        }
        symbols.erase(0, key_prefix.size());
    }
    if (symbols.size() != key_symbols) return std::nullopt;
    std::vector<std::uint8_t> bytes;
    unsigned buffer = 0;
    int bits = 0;
    for (char c : symbols) {
        const int value = symbol_value(c);
        if (value < 0) return std::nullopt;
        buffer = (buffer << 5) | static_cast<unsigned>(value);
        bits += 5;
        if (bits >= 8) {
            bits -= 8;
            bytes.push_back(static_cast<std::uint8_t>(buffer >> bits));
        }
    }
    // Only one spelling of each key: the unused low bits must be zero.
    if (buffer & ((1u << bits) - 1)) return std::nullopt;
    return bytes;
}

bool is_space(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f';
}

} // namespace

PublicKey public_key_for(const SigningSeed& seed) {
    std::uint8_t secret[64];
    SigningSeed copy = seed;  // the key pair function wipes its seed
    PublicKey key{};
    crypto_eddsa_key_pair(secret, key.data(), copy.data());
    crypto_wipe(secret, sizeof secret);
    return key;
}

std::string issue_key(const SigningSeed& seed, std::string_view licensee,
                      int issued_day, std::uint8_t flags) {
    const std::string name = normalize_licensee(licensee);
    if (name.empty()) throw std::invalid_argument("The licensee name or email is empty.");
    if (name.size() > max_licensee_bytes)
        throw std::invalid_argument("The licensee name or email is too long.");
    for (unsigned char c : name)
        if (c < 0x20 || c == 0x7f)
            throw std::invalid_argument("The licensee name contains a control character.");
    const int offset = issued_day - day_base;
    if (offset < 0 || offset > 0xffff) throw std::invalid_argument("The issue date is out of range.");
    std::uint8_t key[key_bytes];
    key[0] = key_version;
    key[1] = flags;
    key[2] = static_cast<std::uint8_t>(offset >> 8);
    key[3] = static_cast<std::uint8_t>(offset);
    std::uint8_t secret[64];
    PublicKey ignored{};
    SigningSeed copy = seed;
    crypto_eddsa_key_pair(secret, ignored.data(), copy.data());
    const auto message = signed_message(key, name);
    crypto_eddsa_sign(key + payload_size, secret, message.data(), message.size());
    crypto_wipe(secret, sizeof secret);
    return encode(key, sizeof key);
}

std::optional<KeyInfo> check_key(std::string_view key, std::string_view licensee,
                                 const PublicKey& public_key, std::string* error) {
    auto fail = [error](const char* reason) -> std::optional<KeyInfo> {
        if (error) *error = reason;
        return std::nullopt;
    };
    const std::string name = normalize_licensee(licensee);
    if (name.empty()) return fail("Enter the name or email the license was issued to.");
    const auto bytes = decode(key);
    if (!bytes) return fail("This is not a license key. Check that the whole key was copied.");
    if ((*bytes)[0] != key_version)
        return fail("This license key is for a different version of the program.");
    const auto message = signed_message(bytes->data(), name);
    if (crypto_eddsa_check(bytes->data() + payload_size, public_key.data(),
                           message.data(), message.size()) != 0)
        return fail("The license key does not match this name. Enter the name or email "
                    "exactly as it appears with the key.");
    KeyInfo info;
    info.licensee = name;
    info.flags = (*bytes)[1];
    info.issued_day = day_base + ((*bytes)[2] << 8 | (*bytes)[3]);
    return info;
}

std::string display_licensee(std::string_view licensee) {
    std::string result;
    bool pending_space = false;
    for (char c : licensee) {
        if (is_space(c)) { pending_space = !result.empty(); continue; }
        if (pending_space) result += ' ';
        pending_space = false;
        result += c;
    }
    return result;
}

std::string normalize_licensee(std::string_view licensee) {
    std::string result = display_licensee(licensee);
    for (char& c : result)
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    return result;
}

int today() {
    using namespace std::chrono;
    return static_cast<int>(floor<days>(system_clock::now()).time_since_epoch().count());
}

std::string format_day(int day) {
    using namespace std::chrono;
    const year_month_day date{sys_days{days{day}}};
    char text[16];
    std::snprintf(text, sizeof text, "%04d-%02u-%02u", static_cast<int>(date.year()),
                  static_cast<unsigned>(date.month()), static_cast<unsigned>(date.day()));
    return text;
}

std::optional<int> parse_day(std::string_view text) {
    using namespace std::chrono;
    int y = 0;
    unsigned m = 0, d = 0;
    char extra = 0;
    const std::string copy(text);
    if (std::sscanf(copy.c_str(), "%4d-%2u-%2u%c", &y, &m, &d, &extra) != 3) return std::nullopt;
    const year_month_day date{year{y}, month{m}, day{d}};
    if (!date.ok()) return std::nullopt;
    return static_cast<int>(sys_days{date}.time_since_epoch().count());
}

std::string to_hex(const std::uint8_t* data, std::size_t size) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string text;
    for (std::size_t i = 0; i < size; ++i) {
        text += digits[data[i] >> 4];
        text += digits[data[i] & 15];
    }
    return text;
}

bool from_hex(std::string_view text, std::uint8_t* out, std::size_t size) {
    if (text.size() != size * 2) return false;
    auto digit = [](char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < size; ++i) {
        const int high = digit(text[2 * i]), low = digit(text[2 * i + 1]);
        if (high < 0 || low < 0) return false;
        out[i] = static_cast<std::uint8_t>(high << 4 | low);
    }
    return true;
}

} // namespace qpv::license
