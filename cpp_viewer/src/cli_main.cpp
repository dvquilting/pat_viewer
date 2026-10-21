#include "qpv/io.hpp"
#include "qpv/license.hpp"

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#endif

namespace fs = std::filesystem;
using qpv::path_text;

namespace {

void usage() {
    std::cout <<
        "Quilt Pattern Viewer converter " QPV_VERSION "\n"
        "Usage:\n"
        "  qpv-cli info <file>\n"
        "  qpv-cli convert <input.pat|qli|svg> <output.pat|qli>\n"
        "  qpv-cli batch <qli-to-pat|svg-to-pat|svg-to-qli> <folder>\n"
        "                [--recursive] [--overwrite]\n";
    if constexpr (qpv::license::enabled)
        std::cout <<
            "  qpv-cli license                  show the license or trial\n"
            "  qpv-cli license install <name-or-email> <key>\n"
            "  qpv-cli license remove\n";
}

// Conversions need a license once the trial has ended; `info` stays free.
bool licensed_for_conversion() {
    const auto status = qpv::license::status();
    if (status.state == qpv::license::State::Trial)
        std::cerr << qpv::license::describe(status) << '\n';
    if (status.unlocked()) return true;
    std::cerr << "Converting needs a license now that the " << qpv::license::trial_length_days
              << "-day trial has ended.\n"
                 "Enter one with: qpv-cli license install \"<name or email>\" <key>\n";
    if (!qpv::license::purchase_url.empty())
        std::cerr << "Buy a license at " << qpv::license::purchase_url << '\n';
    return false;
}

int license_command(const std::vector<fs::path>& args) {
    if constexpr (!qpv::license::enabled) {
        std::cout << "This build does not need a license.\n";
        return 0;
    }
    const std::string action = args.size() > 2 ? path_text(args[2]) : "";
    if (action.empty()) {
        std::cout << qpv::license::describe(qpv::license::status()) << '\n';
        return 0;
    }
    if (action == "install" && args.size() >= 5) {
        // The key may arrive split at its spaces or line breaks.
        std::string key;
        for (std::size_t i = 4; i < args.size(); ++i) key += path_text(args[i]);
        const auto error = qpv::license::install(path_text(args[3]), key);
        if (!error.empty()) {
            std::cerr << error << '\n';
            return 1;
        }
        std::cout << qpv::license::describe(qpv::license::status()) << '\n';
        return 0;
    }
    if (action == "remove" && args.size() == 3) {
        qpv::license::uninstall();
        std::cout << "License removed. " << qpv::license::describe(qpv::license::status()) << '\n';
        return 0;
    }
    usage();
    return 2;
}

// The command line as paths. On Windows argv is in the ANSI code page, which
// cannot hold every file name, so the wide command line is read instead.
std::vector<fs::path> arguments(int argc, char** argv) {
#ifdef _WIN32
    int count = 0;
    if (wchar_t** wide = CommandLineToArgvW(GetCommandLineW(), &count)) {
        std::vector<fs::path> result(wide, wide + count);
        LocalFree(wide);
        return result;
    }
#endif
    return {argv, argv + argc};
}

int batch(const std::vector<fs::path>& args) {
    if (args.size() < 4) { usage(); return 2; }
    if (!licensed_for_conversion()) return 3;
    const std::string operation = path_text(args[2]);
    const fs::path& folder = args[3];
    auto flag = [&args](const char* name) {
        return std::any_of(args.begin() + 4, args.end(),
                           [name](const fs::path& arg) { return path_text(arg) == name; });
    };
    const bool recursive = flag("--recursive");
    const bool overwrite = flag("--overwrite");
    std::string source_ext, output_ext;
    if (operation == "qli-to-pat") { source_ext = ".qli"; output_ext = ".pat"; }
    else if (operation == "svg-to-pat") { source_ext = ".svg"; output_ext = ".pat"; }
    else if (operation == "svg-to-qli") { source_ext = ".svg"; output_ext = ".qli"; }
    else throw std::runtime_error("Unknown batch conversion: " + operation);
    if (!fs::is_directory(folder)) throw std::runtime_error("Not a folder: " + path_text(folder));

    std::size_t converted = 0, skipped = 0, failed = 0;
    auto process = [&](const fs::directory_entry& entry) {
        if (!entry.is_regular_file() || qpv::lower_extension(entry.path()) != source_ext) return;
        auto output = entry.path();
        output.replace_extension(output_ext);
        if (!overwrite && fs::exists(output)) { ++skipped; return; }
        try {
            qpv::convert_file(entry.path(), output);
            ++converted;
            std::cout << path_text(entry.path()) << " -> " << path_text(output) << '\n';
        } catch (const std::exception& e) {
            ++failed;
            std::cerr << path_text(entry.path()) << ": " << e.what() << '\n';
        }
    };
    if (recursive) for (const auto& entry : fs::recursive_directory_iterator(folder)) process(entry);
    else for (const auto& entry : fs::directory_iterator(folder)) process(entry);
    std::cout << "Converted: " << converted << ", skipped: " << skipped
              << ", failed: " << failed << '\n';
    return failed ? 1 : 0;
}

} // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    // Messages are UTF-8, so file names print correctly in the console.
    SetConsoleOutputCP(CP_UTF8);
#endif
    try {
        const auto args = arguments(argc, argv);
        const std::string command = args.size() > 1 ? path_text(args[1]) : "";
        if (args.size() < 2 || command == "--help" || command == "-h") {
            usage();
            return args.size() < 2 ? 2 : 0;
        }
        if (command == "info") {
            if (args.size() != 3) { usage(); return 2; }
            const auto pattern = qpv::load_pattern(args[2]);
            const auto bounds = qpv::pattern_bounds(pattern);
            std::size_t stitches = 0, travel = 0, arcs = 0;
            for (const auto& move : pattern.moves) {
                if (move.gcode == 0) ++travel;
                else if (move.is_stitch()) ++stitches;
                if (move.gcode == 2 || move.gcode == 3) ++arcs;
            }
            std::cout << "File: " << path_text(pattern.filename) << '\n'
                      << "Moves: " << pattern.moves.size() << " (" << stitches
                      << " stitch, " << travel << " travel, " << arcs << " arc)\n"
                      << "Dimensions: " << bounds.width() << " x " << bounds.height()
                      << " PAT units\n"
                      << "Comments: " << pattern.comments.size() << '\n';
            return 0;
        }
        if (command == "convert") {
            if (args.size() != 4) { usage(); return 2; }
            if (!licensed_for_conversion()) return 3;
            qpv::convert_file(args[2], args[3]);
            std::cout << path_text(args[2]) << " -> " << path_text(args[3]) << '\n';
            return 0;
        }
        if (command == "batch") return batch(args);
        if (command == "license") return license_command(args);
        usage();
        return 2;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << '\n';
        return 1;
    }
}
