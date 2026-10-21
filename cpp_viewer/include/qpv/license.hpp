#pragma once

// The license and trial state shared by the viewer and qpv-cli. It lives in a
// small file in the user's application data folder, so the trial and an
// entered key apply to both programs.

#include "qpv/license_config.hpp"
#include "qpv/license_key.hpp"

#include <filesystem>
#include <string>
#include <string_view>

namespace qpv::license {

inline constexpr bool enabled = QPV_LICENSING != 0;
inline constexpr int trial_length_days = QPV_TRIAL_DAYS;
inline constexpr std::string_view purchase_url = QPV_PURCHASE_URL;

enum class State {
    Unrestricted,  // built without QPV_LICENSING
    Licensed,
    Trial,
    TrialEnded,
};

struct Status {
    State state = State::Unrestricted;
    std::string licensee;  // as entered, when Licensed
    int trial_days_left = 0;
    // Whether the features after the trial (conversions, thumbnails and
    // keywords) may be used.
    [[nodiscard]] bool unlocked() const { return state != State::TrialEnded; }
};

[[nodiscard]] std::filesystem::path state_file();

// Reads the state, starting the trial on the first run. Without QPV_LICENSING
// it is always Unrestricted and nothing is read or written.
[[nodiscard]] Status status();
// Checks the key and saves it; returns the reason when it was not accepted.
[[nodiscard]] std::string install(std::string_view licensee, std::string_view key);
void uninstall();
// "Licensed to …", "Trial: 12 days left", "Trial ended", or empty.
[[nodiscard]] std::string describe(const Status& status);

// The same, for any state file, public key and date (used by the tests).
[[nodiscard]] Status evaluate(const std::filesystem::path& file, const PublicKey& key, int today);
[[nodiscard]] std::string install(const std::filesystem::path& file, const PublicKey& key,
                                  std::string_view licensee, std::string_view text, int today);
void uninstall(const std::filesystem::path& file);

} // namespace qpv::license
