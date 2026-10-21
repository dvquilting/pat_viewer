#include "qpv/license.hpp"

#include "qpv/io.hpp"
#include "monocypher.h"

// CMake can point at a public key header elsewhere, for instance a test key.
#ifdef QPV_LICENSE_PUBLIC_KEY_HEADER
#include QPV_LICENSE_PUBLIC_KEY_HEADER
#else
#include "qpv/license_public_key.hpp"
#endif

#if QPV_LICENSING && defined(QPV_LICENSE_PUBLIC_KEY_PLACEHOLDER)
#error "QPV_LICENSING is on, but include/qpv/license_public_key.hpp is still the placeholder. \
Build qpv-keygen (cmake --build build --target qpv-keygen) and run qpv-keygen init; see README.md."
#endif

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <optional>
#include <sstream>
#include <system_error>
#include <thread>

namespace fs = std::filesystem;

namespace qpv::license {
namespace {

constexpr char state_format[] = "qpv-license-state 1";
// Not a secret: it only makes a hand-edited trial date stand out. Deleting the
// file does restart the trial; see README.md.
constexpr char state_check_key[] = "dvquilting trial state";

struct Stored {
    int trial_start = 0;
    int last_seen = 0;
    std::string licensee;
    std::string key;
    bool intact = true;  // false when the file was edited or damaged
};

std::string checksum(const std::string& text) {
    std::uint8_t hash[16];
    crypto_blake2b_keyed(hash, sizeof hash,
                         reinterpret_cast<const std::uint8_t*>(state_check_key),
                         sizeof state_check_key - 1,
                         reinterpret_cast<const std::uint8_t*>(text.data()), text.size());
    return to_hex(hash, sizeof hash);
}

std::optional<int> to_int(const std::string& text) {
    try {
        std::size_t used = 0;
        const int value = std::stoi(text, &used);
        if (used == text.size()) return value;
    } catch (const std::exception&) {}
    return std::nullopt;
}

std::optional<Stored> read_state(const fs::path& file) {
    std::ifstream in(file, std::ios::binary);
    if (!in) {
        std::error_code ignored;
        if (!fs::exists(file, ignored)) return std::nullopt;
        Stored damaged;
        damaged.intact = false;
        return damaged;
    }
    std::stringstream buffer;
    buffer << in.rdbuf();
    const std::string text = buffer.str();
    Stored stored;
    bool has_start = false, has_seen = false, checked = false;
    std::size_t begin = 0;
    bool first = true;
    while (begin < text.size()) {
        std::size_t end = text.find('\n', begin);
        if (end == std::string::npos) end = text.size();
        std::string line = text.substr(begin, end - begin);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        const std::size_t equals = line.find('=');
        const std::string name = line.substr(0, equals);
        const std::string value = equals == std::string::npos ? "" : line.substr(equals + 1);
        if (first) {
            if (line != state_format) stored.intact = false;
            first = false;
        } else if (name == "check") {
            checked = value == checksum(text.substr(0, begin));
            break;
        } else if (name == "trial-start") {
            if (auto day = to_int(value)) { stored.trial_start = *day; has_start = true; }
        } else if (name == "last-seen") {
            if (auto day = to_int(value)) { stored.last_seen = *day; has_seen = true; }
        } else if (name == "licensee") {
            stored.licensee = value;
        } else if (name == "key") {
            stored.key = value;
        }
        begin = end + 1;
    }
    if (!checked || !has_start || !has_seen) stored.intact = false;
    return stored;
}

bool write_state(const fs::path& file, const Stored& stored) {
    std::string text = std::string(state_format) + '\n';
    text += "trial-start=" + std::to_string(stored.trial_start) + '\n';
    text += "last-seen=" + std::to_string(stored.last_seen) + '\n';
    if (!stored.key.empty()) {
        text += "licensee=" + stored.licensee + '\n';
        text += "key=" + stored.key + '\n';
    }
    text += "check=" + checksum(text) + '\n';
    std::error_code error;
    fs::create_directories(file.parent_path(), error);
    fs::path temporary = file;
    temporary += ".tmp";
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        if (!(out << text) || !out.flush()) return false;
    }
    // As in the viewer, the rename can fail for a moment on Windows while
    // antivirus or the indexer has the new file open.
    for (int attempt = 0; attempt < 5; ++attempt) {
        if (attempt) std::this_thread::sleep_for(std::chrono::milliseconds(10 << attempt));
        fs::rename(temporary, file, error);
        if (!error) return true;
    }
    fs::remove(temporary, error);
    return false;
}

// The stored state brought up to `today`, starting the trial if there is none.
// The date never goes backwards, so setting the clock back gains nothing.
Stored current(const fs::path& file, int today, bool& changed) {
    auto stored = read_state(file);
    changed = !stored || !stored->intact;
    if (!stored) {
        stored.emplace();
        stored->trial_start = today;
        stored->last_seen = today;
    } else if (!stored->intact) {
        const int seen = std::max(today, stored->last_seen);
        stored->trial_start = seen - trial_length_days;
        stored->last_seen = seen;
        stored->intact = true;
    } else if (today > stored->last_seen) {
        stored->last_seen = today;
        changed = true;
    }
    return *stored;
}

} // namespace

fs::path state_file() {
    fs::path root;
#ifdef _WIN32
    if (const wchar_t* appdata = _wgetenv(L"APPDATA")) root = appdata;
#else
    const char* home = std::getenv("HOME");
#ifdef __APPLE__
    if (home) root = fs::path(home) / "Library" / "Application Support";
#else
    if (const char* config = std::getenv("XDG_CONFIG_HOME"); config && *config) root = config;
    else if (home) root = fs::path(home) / ".config";
#endif
#endif
    if (root.empty()) {
        std::error_code ignored;
        root = fs::temp_directory_path(ignored);
    }
    return root / "dvquilting" / "QuiltPatternViewer" / "license.dat";
}

Status evaluate(const fs::path& file, const PublicKey& key, int today) {
    bool changed = false;
    const Stored stored = current(file, today, changed);
    if (changed) write_state(file, stored);
    Status status;
    if (!stored.key.empty() && check_key(stored.key, stored.licensee, key)) {
        status.state = State::Licensed;
        status.licensee = stored.licensee;
        return status;
    }
    status.trial_days_left =
        std::max(0, trial_length_days - (stored.last_seen - stored.trial_start));
    status.state = status.trial_days_left > 0 ? State::Trial : State::TrialEnded;
    return status;
}

std::string install(const fs::path& file, const PublicKey& key, std::string_view licensee,
                    std::string_view text, int today) {
    std::string error;
    if (!check_key(text, licensee, key, &error)) return error;
    bool changed = false;
    Stored stored = current(file, today, changed);
    stored.licensee = display_licensee(licensee);
    stored.key.clear();
    for (char c : text) {
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') continue;
        stored.key += c >= 'a' && c <= 'z' ? static_cast<char>(c - 'a' + 'A') : c;
    }
    if (!write_state(file, stored))
        return "The license key is valid, but it could not be saved to " + path_text(file) + '.';
    return {};
}

void uninstall(const fs::path& file) {
    bool changed = false;
    Stored stored = current(file, today(), changed);
    stored.licensee.clear();
    stored.key.clear();
    write_state(file, stored);
}

Status status() {
    if constexpr (!enabled) return {};
    return evaluate(state_file(), embedded_public_key, today());
}

std::string install(std::string_view licensee, std::string_view key) {
    if constexpr (!enabled) return {};
    return install(state_file(), embedded_public_key, licensee, key, today());
}

void uninstall() {
    if constexpr (enabled) uninstall(state_file());
}

std::string describe(const Status& status) {
    switch (status.state) {
    case State::Unrestricted: return {};
    case State::Licensed: return "Licensed to " + status.licensee;
    case State::Trial:
        return "Trial: " + std::to_string(status.trial_days_left) +
               (status.trial_days_left == 1 ? " day left" : " days left");
    case State::TrialEnded: return "Trial ended";
    }
    return {};
}

} // namespace qpv::license
