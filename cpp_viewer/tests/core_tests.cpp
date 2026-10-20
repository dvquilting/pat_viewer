#include "qpv/io.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace fs = std::filesystem;

namespace {
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void close(double actual, double expected, double tolerance, const char* message) {
    if (std::abs(actual - expected) > tolerance) throw std::runtime_error(message);
}
void write(const fs::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    out << text;
}
} // namespace

int main() {
    try {
        const auto dir = fs::temp_directory_path() / "qpv_core_tests";
        fs::create_directories(dir);

        const auto pat_path = dir / "source.pat";
        write(pat_path,
              "N10G00X0.0000Y0.0000\r\n"
              "N11G01X2.0000Y0.0000\r\n"
              "N12G03X0.0000Y0.0000I1.0000J0.0000\r\n"
              "N13M02\r\n( sample )\r\n");
        auto pat = qpv::read_pat(pat_path);
        check(pat.moves.size() == 3, "PAT move count");
        check(pat.moves[2].center.has_value(), "PAT arc center");
        check(pat.comments == std::vector<std::string>{"sample"}, "PAT comments");
        close(qpv::pattern_bounds(pat).width(), 2.0, 1e-6, "PAT bounds");

        const auto flexible_pat_path = dir / "flexible.pat";
        write(flexible_pat_path,
              "  n20  g00  x+1.5000  y-.2500\r\n"
              "n21 g02 x2.5000 y+.7500 i+2.0000 j-.2500\r\n"
              "n22 m02\r\n"
              "  (  mixed case and spacing  )  \r\n");
        const auto flexible_pat = qpv::read_pat(flexible_pat_path);
        check(flexible_pat.moves.size() == 2, "Flexible PAT move count");
        check(flexible_pat.moves[1].center.has_value(), "Flexible PAT arc center");
        check(flexible_pat.end_sequence == 22, "Flexible PAT end sequence");
        check(flexible_pat.comments ==
                  std::vector<std::string>{"mixed case and spacing"},
              "Flexible PAT comment");
        close(flexible_pat.moves[0].end.x, 1.5, 1e-9, "PAT leading plus");

        const auto roundtrip = dir / "roundtrip.pat";
        qpv::write_pat(pat, roundtrip);
        check(qpv::read_pat(roundtrip).moves.size() == pat.moves.size(), "PAT round trip");

        const auto qli_path = dir / "source.qli";
        write(qli_path, "REM hello\nNO NEEDLE OFF\nVP 1000,0\nNO NEEDLE ON\n"
                        "VP 2000,1000\nCR 1000,0,180\nEN\n");
        auto qli = qpv::read_qli(qli_path);
        check(qli.moves.size() == 4, "QLI move count");
        close(qli.moves[1].end.x, 1.0, 1e-9, "QLI scale");
        check(qli.moves.back().gcode == 3, "QLI arc direction");
        const auto qli_roundtrip = dir / "roundtrip.qli";
        qpv::write_qli(qli, qli_roundtrip);
        check(!qpv::read_qli(qli_roundtrip).moves.empty(), "QLI round trip");

        const auto svg_path = dir / "source.svg";
        write(svg_path,
              "<svg width=\"254mm\" height=\"127mm\" viewBox=\"0 0 100 50\">"
              "<path d=\"M0 25 L100 25 M10 25 A40 40 0 0 1 90 25\" "
              "fill=\"none\" stroke=\"black\"/></svg>");
        auto svg = qpv::read_svg(svg_path);
        check(svg.moves.size() >= 4, "SVG moves");
        close(qpv::pattern_bounds(svg).width(), 10.0, 0.01, "SVG physical scale");
        check(std::any_of(svg.moves.begin(), svg.moves.end(),
                          [](const qpv::Move& m) { return m.gcode == 2 || m.gcode == 3; }),
              "SVG circular arc");

        close(qpv::arc_sweep(0, 0, true), 180, 1e-12, "CCW degenerate sweep");
        close(qpv::arc_sweep(0, 0, false), -180, 1e-12, "CW degenerate sweep");
        std::cout << "All core tests passed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Test failure: " << e.what() << '\n';
        return 1;
    }
}
