// qpv-keygen: makes the license signing key pair and issues license keys.
// It is for the seller only and is not part of the installer.

#include "qpv/license_key.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <shellapi.h>
#endif

namespace fs = std::filesystem;
namespace lic = qpv::license;

namespace {

constexpr char key_file_format[] = "qpv-signing-key 1";

void usage() {
    std::cout <<
        "License key generator for Quilt Pattern Viewer\n"
        "Usage:\n"
        "  qpv-keygen init <private-key-file> [--header <license_public_key.hpp>]\n"
        "      Makes a new signing key pair. Keep the private key file safe and\n"
        "      secret: it is the only way to issue keys the programs accept.\n"
        "  qpv-keygen header <private-key-file> [--header <license_public_key.hpp>]\n"
        "      Writes the public key header again (to the console without --header).\n"
        "  qpv-keygen issue <private-key-file> <name-or-email> [--date YYYY-MM-DD]\n"
        "                   [--log <issued.csv>]\n"
        "      Issues a license key for one customer.\n"
        "  qpv-keygen verify <private-key-file> <name-or-email> <key>\n"
        "      Checks a key against a name.\n";
}

std::string utf8(const fs::path& path) {
    const auto text = path.u8string();
    return {text.begin(), text.end()};
}

fs::path from_utf8(const std::string& text) {
    return fs::path(std::u8string(text.begin(), text.end()));
}

// The command line as UTF-8, so customer names outside the ANSI code page
// arrive intact on Windows.
std::vector<std::string> arguments(int argc, char** argv) {
#ifdef _WIN32
    int count = 0;
    if (wchar_t** wide = CommandLineToArgvW(GetCommandLineW(), &count)) {
        std::vector<std::string> result;
        for (int i = 0; i < count; ++i) result.push_back(utf8(fs::path(wide[i])));
        LocalFree(wide);
        return result;
    }
#endif
    return {argv, argv + argc};
}

lic::SigningSeed random_seed() {
    lic::SigningSeed seed{};
#ifdef _WIN32
    if (BCryptGenRandom(nullptr, seed.data(), static_cast<ULONG>(seed.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        throw std::runtime_error("The system random number generator failed.");
#else
    std::ifstream random("/dev/urandom", std::ios::binary);
    if (!random.read(reinterpret_cast<char*>(seed.data()), seed.size()))
        throw std::runtime_error("Could not read /dev/urandom.");
#endif
    return seed;
}

lic::SigningSeed read_seed(const fs::path& file) {
    std::ifstream in(file, std::ios::binary);
    if (!in) throw std::runtime_error("Cannot open the private key file " + utf8(file));
    std::string line, seed_hex, public_hex;
    bool format = false;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line == key_file_format) format = true;
        else if (line.rfind("seed=", 0) == 0) seed_hex = line.substr(5);
        else if (line.rfind("public=", 0) == 0) public_hex = line.substr(7);
    }
    lic::SigningSeed seed{};
    lic::PublicKey expected{};
    if (!format || !lic::from_hex(seed_hex, seed.data(), seed.size()) ||
        !lic::from_hex(public_hex, expected.data(), expected.size()))
        throw std::runtime_error(utf8(file) + " is not a qpv-keygen private key file.");
    if (lic::public_key_for(seed) != expected)
        throw std::runtime_error(utf8(file) + " is damaged: its two halves do not match.");
    return seed;
}

std::string header_text(const lic::PublicKey& key) {
    std::ostringstream out;
    out << "#pragma once\n\n"
           "// The public half of the license signing key, written by qpv-keygen.\n"
           "// It checks license keys; it cannot make them, so it is safe to publish.\n"
           "// Key id: " << lic::to_hex(key.data(), 4) << "\n\n"
           "#include <array>\n"
           "#include <cstdint>\n\n"
           "namespace qpv::license {\n\n"
           "inline constexpr std::array<std::uint8_t, 32> embedded_public_key{\n";
    for (std::size_t i = 0; i < key.size(); ++i) {
        if (i % 8 == 0) out << "    ";
        out << "0x" << lic::to_hex(&key[i], 1) << (i + 1 < key.size() ? "," : "");
        out << (i % 8 == 7 ? "\n" : " ");
    }
    out << "};\n\n} // namespace qpv::license\n";
    return out.str();
}

void write_header(const lic::PublicKey& key, const std::string& destination) {
    if (destination.empty()) {
        std::cout << header_text(key);
        return;
    }
    std::ofstream out(from_utf8(destination), std::ios::binary | std::ios::trunc);
    if (!(out << header_text(key)) || !out.flush())
        throw std::runtime_error("Cannot write " + destination);
    std::cout << "Public key header written to " << destination << '\n';
}

// The value after `name` among the arguments from `first` on, or empty.
std::string option(const std::vector<std::string>& args, std::size_t first, const char* name) {
    for (std::size_t i = first; i + 1 < args.size(); ++i)
        if (args[i] == name) return args[i + 1];
    return {};
}

std::string csv_field(const std::string& text) {
    std::string quoted = "\"";
    for (char c : text) quoted += c == '"' ? std::string("\"\"") : std::string(1, c);
    return quoted + '"';
}

int init(const std::vector<std::string>& args) {
    if (args.size() < 3) { usage(); return 2; }
    const fs::path file = from_utf8(args[2]);
    // A new pair would silently invalidate every key issued with the old one.
    if (fs::exists(file))
        throw std::runtime_error(args[2] + " already exists. Refusing to replace a signing key.");
    const auto seed = random_seed();
    const auto key = lic::public_key_for(seed);
    {
        std::ofstream out(file, std::ios::binary);
        out << key_file_format << '\n'
            << "# Quilt Pattern Viewer license signing key. Keep it secret and backed up:\n"
            << "# anyone with this file can make license keys, and without it no more\n"
            << "# keys can be made for the programs already sold.\n"
            << "seed=" << lic::to_hex(seed.data(), seed.size()) << '\n'
            << "public=" << lic::to_hex(key.data(), key.size()) << '\n';
        if (!out.flush()) throw std::runtime_error("Cannot write " + args[2]);
    }
#ifndef _WIN32
    fs::permissions(file, fs::perms::owner_read | fs::perms::owner_write);
#endif
    std::cout << "Private signing key written to " << args[2] << "\n"
                 "Key id: " << lic::to_hex(key.data(), 4) << "\n"
                 "Back it up somewhere safe, and never add it to the git repository.\n";
    write_header(key, option(args, 3, "--header"));
    return 0;
}

int issue(const std::vector<std::string>& args) {
    if (args.size() < 4) { usage(); return 2; }
    const auto seed = read_seed(from_utf8(args[2]));
    const std::string name = lic::display_licensee(args[3]);
    int day = lic::today();
    if (const auto date = option(args, 4, "--date"); !date.empty()) {
        const auto parsed = lic::parse_day(date);
        if (!parsed) throw std::runtime_error("Not a date (YYYY-MM-DD): " + date);
        day = *parsed;
    }
    const std::string key = lic::issue_key(seed, name, day);
    // Checked with the public key alone, exactly as the programs will.
    if (!lic::check_key(key, name, lic::public_key_for(seed)))
        throw std::runtime_error("The new key failed its own check.");
    std::cout << "Licensed to: " << name << '\n'
              << "License key: " << key << '\n';
    if (const auto log = option(args, 4, "--log"); !log.empty()) {
        const fs::path path = from_utf8(log);
        const bool fresh = !fs::exists(path);
        std::ofstream out(path, std::ios::binary | std::ios::app);
        if (fresh) out << "issued,licensee,key\r\n";
        out << lic::format_day(day) << ',' << csv_field(name) << ',' << key << "\r\n";
        if (!out.flush()) throw std::runtime_error("Cannot write " + log);
    }
    return 0;
}

int verify(const std::vector<std::string>& args) {
    if (args.size() < 5) { usage(); return 2; }
    const auto seed = read_seed(from_utf8(args[2]));
    std::string key;
    for (std::size_t i = 4; i < args.size(); ++i) key += args[i];
    std::string error;
    const auto info = lic::check_key(key, args[3], lic::public_key_for(seed), &error);
    if (!info) {
        std::cout << "Not valid: " << error << '\n';
        return 1;
    }
    std::cout << "Valid for: " << info->licensee << '\n'
              << "Issued: " << lic::format_day(info->issued_day) << '\n';
    return 0;
}

} // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
#endif
    try {
        const auto args = arguments(argc, argv);
        const std::string command = args.size() > 1 ? args[1] : "";
        if (command == "init") return init(args);
        if (command == "issue") return issue(args);
        if (command == "verify") return verify(args);
        if (command == "header") {
            if (args.size() < 3) { usage(); return 2; }
            write_header(lic::public_key_for(read_seed(from_utf8(args[2]))),
                         option(args, 3, "--header"));
            return 0;
        }
        usage();
        return command == "--help" || command == "-h" ? 0 : 2;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << '\n';
        return 1;
    }
}
