#include "qpv/io.hpp"

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;

namespace {

void usage() {
    std::cout <<
        "Quilt Pattern Viewer converter 1.0\n"
        "Usage:\n"
        "  qpv-cli info <file>\n"
        "  qpv-cli convert <input.pat|qli|svg> <output.pat|qli>\n"
        "  qpv-cli batch <qli-to-pat|svg-to-pat|svg-to-qli> <folder>\n"
        "                [--recursive] [--overwrite]\n";
}

int batch(int argc, char** argv) {
    if (argc < 4) { usage(); return 2; }
    const std::string operation = argv[2];
    const fs::path folder = argv[3];
    const bool recursive = std::find(argv + 4, argv + argc, std::string("--recursive")) != argv + argc;
    const bool overwrite = std::find(argv + 4, argv + argc, std::string("--overwrite")) != argv + argc;
    std::string source_ext, output_ext;
    if (operation == "qli-to-pat") { source_ext = ".qli"; output_ext = ".pat"; }
    else if (operation == "svg-to-pat") { source_ext = ".svg"; output_ext = ".pat"; }
    else if (operation == "svg-to-qli") { source_ext = ".svg"; output_ext = ".qli"; }
    else throw std::runtime_error("Unknown batch conversion: " + operation);
    if (!fs::is_directory(folder)) throw std::runtime_error("Not a folder: " + folder.string());

    std::size_t converted = 0, skipped = 0, failed = 0;
    auto process = [&](const fs::directory_entry& entry) {
        if (!entry.is_regular_file() || qpv::lower_extension(entry.path()) != source_ext) return;
        auto output = entry.path();
        output.replace_extension(output_ext);
        if (!overwrite && fs::exists(output)) { ++skipped; return; }
        try {
            qpv::convert_file(entry.path(), output);
            ++converted;
            std::cout << entry.path().string() << " -> " << output.string() << '\n';
        } catch (const std::exception& e) {
            ++failed;
            std::cerr << entry.path().string() << ": " << e.what() << '\n';
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
    try {
        if (argc < 2 || std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h") {
            usage();
            return argc < 2 ? 2 : 0;
        }
        const std::string command = argv[1];
        if (command == "info") {
            if (argc != 3) { usage(); return 2; }
            const auto pattern = qpv::load_pattern(argv[2]);
            const auto bounds = qpv::pattern_bounds(pattern);
            std::size_t stitches = 0, travel = 0, arcs = 0;
            for (const auto& move : pattern.moves) {
                if (move.gcode == 0) ++travel;
                else if (move.is_stitch()) ++stitches;
                if (move.gcode == 2 || move.gcode == 3) ++arcs;
            }
            std::cout << "File: " << pattern.filename.string() << '\n'
                      << "Moves: " << pattern.moves.size() << " (" << stitches
                      << " stitch, " << travel << " travel, " << arcs << " arc)\n"
                      << "Dimensions: " << bounds.width() << " x " << bounds.height()
                      << " PAT units\n"
                      << "Comments: " << pattern.comments.size() << '\n';
            return 0;
        }
        if (command == "convert") {
            if (argc != 4) { usage(); return 2; }
            qpv::convert_file(argv[2], argv[3]);
            std::cout << argv[2] << " -> " << argv[3] << '\n';
            return 0;
        }
        if (command == "batch") return batch(argc, argv);
        usage();
        return 2;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << '\n';
        return 1;
    }
}
