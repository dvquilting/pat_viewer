#include "qpv/license.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;
namespace lic = qpv::license;

namespace {
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
std::string read(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::stringstream text;
    text << in.rdbuf();
    return text.str();
}
void write(const fs::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    out << text;
}
std::string replaced(std::string text, const std::string& from, const std::string& to) {
    const auto at = text.find(from);
    if (at == std::string::npos) throw std::runtime_error("test text not found: " + from);
    return text.replace(at, from.size(), to);
}
} // namespace

int main() {
    try {
        lic::SigningSeed seed{};
        for (std::size_t i = 0; i < seed.size(); ++i) seed[i] = static_cast<std::uint8_t>(i + 1);
        const auto public_key = lic::public_key_for(seed);
        lic::SigningSeed other_seed = seed;
        other_seed[0] ^= 1;
        const auto other_key = lic::public_key_for(other_seed);
        check(public_key != other_key, "Distinct seeds give distinct keys");

        const std::string name = "Jane Doe <jane@example.com>";
        const int issued = *lic::parse_day("2026-10-20");
        const std::string key = lic::issue_key(seed, name, issued);
        check(key.rfind("QPV1-", 0) == 0, "Key prefix");
        check(key == lic::issue_key(seed, name, issued), "Keys are deterministic");

        // Keys issued by earlier builds must keep working: this pins the format.
        check(key == "QPV1-0400K-D7HZS-MNR67-09W42-997DP-054WF-ZA4ZV-RYK3R-0VSD3-ZNXH8-J2SZG-"
                     "5R0ZZ-WBBJB-AXR5G-R7BBW-R38KX-JFMAE-Q3BAK-21RQG-QWP0B-V86WP-2F02",
              "Known key");

        const auto info = lic::check_key(key, name, public_key);
        check(info.has_value(), "Key accepted");
        check(info->licensee == "jane doe <jane@example.com>", "Normalised licensee");
        check(info->issued_day == issued, "Issue date");
        check(lic::format_day(info->issued_day) == "2026-10-20", "Issue date text");

        check(lic::check_key(key, "  jane   DOE <JANE@example.com>\n", public_key).has_value(),
              "Name case and spacing ignored");
        std::string loose;
        for (char c : key)
            if (c != '-') loose += static_cast<char>(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
        check(lic::check_key(loose, name, public_key).has_value(), "Lower case, no dashes");
        check(lic::check_key(" " + key.substr(5) + "\r\n", name, public_key).has_value(),
              "Key without its prefix");
        std::string misread = key;
        for (char& c : misread) if (c == '0') c = 'O'; else if (c == '1') c = 'l';
        check(lic::check_key(misread, name, public_key).has_value(), "O and l read as 0 and 1");

        std::string error;
        check(!lic::check_key(key, "Jane Doe", public_key, &error), "Other name rejected");
        check(error.find("does not match") != std::string::npos, "Name mismatch message");
        check(!lic::check_key(key, name, other_key), "Other public key rejected");
        std::string tampered = key;
        tampered[10] = tampered[10] == 'X' ? 'Y' : 'X';
        check(!lic::check_key(tampered, name, public_key), "Changed symbol rejected");
        check(!lic::check_key(key.substr(0, key.size() - 1), name, public_key, &error),
              "Short key rejected");
        check(error.find("not a license key") != std::string::npos, "Short key message");
        check(!lic::check_key(key + "0", name, public_key), "Long key rejected");
        check(!lic::check_key(key, "   ", public_key), "Empty name rejected");
        bool threw = false;
        try { (void)lic::issue_key(seed, " ", issued); } catch (const std::invalid_argument&) { threw = true; }
        check(threw, "Empty name cannot be issued");

        check(!lic::parse_day("2026-02-30"), "Invalid date");
        check(lic::parse_day("1970-01-01") == 0, "Epoch day");

        // The trial and license state file.
        const auto dir = fs::temp_directory_path() / "qpv_license_tests";
        fs::remove_all(dir);
        const auto file = dir / "license.dat";
        const int start = issued;
        auto status = lic::evaluate(file, public_key, start);
        check(status.state == lic::State::Trial, "Trial starts on first run");
        check(status.trial_days_left == lic::trial_length_days, "Full trial");
        check(fs::exists(file), "State saved");
        check(lic::describe(status) == "Trial: 30 days left", "Trial description");

        status = lic::evaluate(file, public_key, start + 10);
        check(status.trial_days_left == 20, "Trial counts down");
        status = lic::evaluate(file, public_key, start + 5);
        check(status.trial_days_left == 20, "Setting the clock back gains nothing");
        status = lic::evaluate(file, public_key, start + 29);
        check(status.trial_days_left == 1 && status.unlocked(), "Last trial day");
        check(lic::describe(status) == "Trial: 1 day left", "Singular day");
        status = lic::evaluate(file, public_key, start + 30);
        check(status.state == lic::State::TrialEnded && !status.unlocked(), "Trial ends");
        check(lic::describe(status) == "Trial ended", "Ended description");

        check(!lic::install(file, public_key, "Someone Else", key, start + 30).empty(),
              "Wrong name not installed");
        check(lic::evaluate(file, public_key, start + 30).state == lic::State::TrialEnded,
              "Still ended after a rejected key");
        check(lic::install(file, public_key, "  Jane  Doe <jane@example.com> ", key, start + 31)
                  .empty(), "Key installed");
        status = lic::evaluate(file, public_key, start + 31);
        check(status.state == lic::State::Licensed && status.unlocked(), "Licensed");
        check(status.licensee == "Jane Doe <jane@example.com>", "Licensee as entered");
        check(lic::describe(status) == "Licensed to Jane Doe <jane@example.com>",
              "Licensed description");
        check(lic::evaluate(file, other_key, start + 31).state == lic::State::TrialEnded,
              "Stored key checked against the public key");

        // Editing the stored name breaks the signature as well as the check.
        const std::string licensed = read(file);
        write(file, replaced(licensed, "licensee=Jane", "licensee=John"));
        check(lic::evaluate(file, public_key, start + 31).state == lic::State::TrialEnded,
              "Edited licensee rejected");
        write(file, licensed);
        check(lic::evaluate(file, public_key, start + 31).state == lic::State::Licensed,
              "Restored file licensed");

        lic::uninstall(file);
        check(lic::evaluate(file, public_key, start + 31).state == lic::State::TrialEnded,
              "Uninstalled license returns to the trial state");

        // A hand-edited trial date ends the trial rather than extending it.
        fs::remove(file);
        (void)lic::evaluate(file, public_key, start);
        write(file, replaced(read(file), "trial-start=" + std::to_string(start),
                             "trial-start=" + std::to_string(start + 100)));
        check(lic::evaluate(file, public_key, start + 1).state == lic::State::TrialEnded,
              "Edited trial date ends the trial");
        write(file, "garbage");
        check(lic::evaluate(file, public_key, start).state == lic::State::TrialEnded,
              "Damaged file ends the trial");
        // ...but a valid key still installs over it.
        check(lic::install(file, public_key, name, key, start).empty(), "Install over damage");
        check(lic::evaluate(file, public_key, start).state == lic::State::Licensed,
              "Licensed after damage");

        if constexpr (!lic::enabled) {
            check(lic::status().state == lic::State::Unrestricted, "Unrestricted build");
            check(lic::status().unlocked() && lic::describe(lic::status()).empty(),
                  "Unrestricted build unlocked");
        }
        fs::remove_all(dir);
        std::cout << "All license tests passed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAILED: " << e.what() << '\n';
        return 1;
    }
}
