#include "qpv/io.hpp"

#include <algorithm>
#include <clocale>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

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

        // Coincident arc endpoints are a full circle and must survive conversion.
        close(qpv::arc_sweep(0, 0, true), 360, 1e-12, "CCW full-circle sweep");
        close(qpv::arc_sweep(0, 0, false), -360, 1e-12, "CW full-circle sweep");
        const auto circle_qli = dir / "circle.qli";
        write(circle_qli, "VP 1000,0\nCR 1000,0,360\nVP 5000,0\nEN\n");
        const auto circle_pat = dir / "circle.pat";
        qpv::write_pat(qpv::read_qli(circle_qli), circle_pat);
        const auto circle = qpv::read_pat(circle_pat);
        close(qpv::pattern_bounds(circle).height(), 2.0, 1e-3, "PAT full circle height");
        const auto circle_back = dir / "circle_back.qli";
        qpv::write_qli(circle, circle_back);
        const auto circle_reread = qpv::read_qli(circle_back);
        const auto circle_arc = std::find_if(
            circle_reread.moves.begin(), circle_reread.moves.end(),
            [](const qpv::Move& m) { return m.gcode == 2 || m.gcode == 3; });
        check(circle_arc != circle_reread.moves.end(), "QLI full circle arc");
        close(circle_arc->end.x, 1.0, 1e-6, "QLI full circle end x");
        close(circle_arc->end.y, 0.0, 1e-6, "QLI full circle end y");

        const auto signed_qli = dir / "signed.qli";
        write(signed_qli, "VP +1000,-500\nEN\n");
        const auto signed_moves = qpv::read_qli(signed_qli).moves;
        close(signed_moves[1].end.x, 1.0, 1e-9, "QLI leading plus");
        close(signed_moves[1].end.y, -0.5, 1e-9, "QLI negative");

        // Arc flags may be written without separators: "0110" is 0, 1, then 10.
        const auto flags_svg = dir / "flags.svg";
        write(flags_svg, "<svg width=\"100in\" viewBox=\"0 0 100 100\">"
                         "<path d=\"M10 10a5 5 0 0110 0\" stroke=\"black\"/></svg>");
        const auto flags = qpv::read_svg(flags_svg);
        check(std::any_of(flags.moves.begin(), flags.moves.end(),
                          [](const qpv::Move& m) { return m.gcode == 2 || m.gcode == 3; }),
              "SVG compact arc flags");
        close(qpv::pattern_bounds(flags).width(), 10.0, 1e-3, "SVG compact arc width");
        close(qpv::pattern_bounds(flags).height(), 10.0, 1e-3, "SVG compact arc direction");

        // A clearly elliptical arc must be flattened even in small user units.
        const auto ellipse_svg = dir / "ellipse.svg";
        write(ellipse_svg, "<svg width=\"1in\" viewBox=\"0 0 1 1\">"
                           "<path d=\"M0 0.5 A0.3 0.1 0 0 1 0.6 0.5\" stroke=\"black\"/></svg>");
        const auto ellipse = qpv::read_svg(ellipse_svg);
        check(std::none_of(ellipse.moves.begin(), ellipse.moves.end(),
                           [](const qpv::Move& m) { return m.gcode == 2 || m.gcode == 3; }),
              "SVG ellipse is not a circle");
        close(qpv::pattern_bounds(ellipse).height(), 0.1, 0.005, "SVG ellipse height");

        // Attribute names match exactly, and quoted values may contain '>'.
        const auto attrs_svg = dir / "attributes.svg";
        write(attrs_svg, "<svg stroke-width=\"2\" width=\"254mm\" viewBox=\"0 0 100 50\">"
                         "<path data-note=\"a>b\" data-d=\"M0 0\" d=\"M0 25 L100 25\" "
                         "fill=\"none\" stroke=\"black\"/></svg>");
        close(qpv::pattern_bounds(qpv::read_svg(attrs_svg)).width(), 10.0, 0.01,
              "SVG attribute parsing");

        // Long path data used to overflow the stack inside std::regex.
        std::string long_path = "M0 0";
        for (int i = 0; i < 200000; ++i)
            long_path += " L" + std::to_string(i % 100) + ' ' + std::to_string(i * 7 % 100);
        const auto long_svg = dir / "long.svg";
        write(long_svg, "<svg width=\"100\" viewBox=\"0 0 100 100\"><path stroke=\"black\" d=\"" +
                            long_path + "\"/></svg>");
        check(qpv::read_svg(long_svg).moves.size() > 100000, "SVG long path");

        // Comments must not be read back as commands.
        qpv::Pattern commented;
        commented.moves = {{10, 0, {0.0, 0.0}, std::nullopt}, {11, 1, {1.0, 1.0}, std::nullopt}};
        commented.comments = {"Designed by Ann; VP 99000,99000",
                              "Line one\r\nN99G01X5Y5 (note)"};
        const auto commented_qli = dir / "commented.qli";
        qpv::write_qli(commented, commented_qli);
        const auto commented_qli_back = qpv::read_qli(commented_qli);
        check(commented_qli_back.moves.size() == 3, "QLI comment injection");
        check(commented_qli_back.comments.size() == 2, "QLI comments kept");
        const auto commented_pat = dir / "commented.pat";
        qpv::write_pat(commented, commented_pat);
        const auto commented_pat_back = qpv::read_pat(commented_pat);
        check(commented_pat_back.moves.size() == 2, "PAT comment injection");
        check(commented_pat_back.comments.size() == 2, "PAT comments kept");

        // File names outside the ANSI code page work, and errors name them in UTF-8.
        const auto unicode_pat = dir / fs::path(u8"Café üł.pat");
        write(unicode_pat, "N10G00X0Y0\r\nN11G01X1Y1\r\n");
        check(qpv::load_pattern(unicode_pat).moves.size() == 2, "Non-ASCII PAT path");
        const auto unicode_qli = dir / fs::path(u8"Blüte 中.qli");
        qpv::convert_file(unicode_pat, unicode_qli);
        check(qpv::read_qli(unicode_qli).moves.size() == 3, "Non-ASCII QLI path");
        try {
            (void)qpv::read_pat(dir / fs::path(u8"Fehlt ä.pat"));
            check(false, "Missing file did not throw");
        } catch (const std::runtime_error& e) {
            check(std::string(e.what()).find("Fehlt \xC3\xA4.pat") != std::string::npos,
                  "UTF-8 file name in error message");
        }

        // Arcs without a centre are written as the straight stitches they are
        // drawn as, and other codes are kept rather than dropped.
        qpv::Pattern odd;
        odd.moves = {{10, 0, {0.0, 0.0}, std::nullopt}, {11, 2, {1.0, 0.0}, std::nullopt},
                     {12, 92, {2.0, 0.0}, std::nullopt}};
        const auto odd_pat = dir / "odd.pat";
        qpv::write_pat(odd, odd_pat);
        const auto odd_back = qpv::read_pat(odd_pat);
        check(odd_back.moves.size() == 3, "PAT writer keeps every move");
        check(odd_back.moves[1].gcode == 1, "Centre-less arc written as G01");
        check(odd_back.moves[2].gcode == 92, "Unknown G-code kept");

        // An arc made with the needle off is travel.
        const auto needle_qli = dir / "needle.qli";
        write(needle_qli, "NO NEEDLE OFF\nCR 1000,0,90\nEN\n");
        const auto needle = qpv::read_qli(needle_qli).moves.back();
        check(needle.gcode == 0 && !needle.center, "QLI arc with needle off is travel");

        // SVG: transforms, styles, hidden content, basic shapes and frames.
        // width="100in" with a 100-unit viewBox makes one user unit one PAT unit.
        auto svg_doc = [&dir](const char* name, const std::string& body) {
            const auto file = dir / name;
            write(file, "<svg width=\"100in\" viewBox=\"0 0 100 100\">" + body + "</svg>");
            return qpv::read_svg(file);
        };
        auto has_arc = [](const qpv::Pattern& p) {
            return std::any_of(p.moves.begin(), p.moves.end(),
                               [](const qpv::Move& m) { return m.gcode == 2 || m.gcode == 3; });
        };
        auto stitches = [](const qpv::Pattern& p) {
            return std::count_if(p.moves.begin(), p.moves.end(),
                                 [](const qpv::Move& m) { return m.gcode != 0; });
        };
        const auto moved = svg_doc("transform.svg",
            "<g transform=\"translate(10,0) scale(2)\"><path stroke=\"black\" d=\"M0 0 L5 0\"/></g>"
            "<path stroke=\"black\" d=\"M0 0 L1 0\"/>");
        close(qpv::pattern_bounds(moved).width(), 20.0, 1e-6, "SVG group transform");

        const auto flipped = svg_doc("flipped.svg",
            "<g transform=\"scale(1,-1)\"><path stroke=\"black\" d=\"M0 0 L10 10\"/></g>");
        check(flipped.moves.back().end.y > flipped.moves[flipped.moves.size() - 2].end.y,
              "SVG flipped group points up");

        const auto styled = svg_doc("styled.svg",
            "<g style=\"stroke:red\"><path d=\"M0 0 L50 0\"/></g>"
            "<path style=\"fill:#000;stroke:none\" d=\"M0 0 L50 50\"/>"
            "<defs><path stroke=\"black\" d=\"M0 0 L80 0\"/></defs>"
            "<path stroke=\"black\" d=\"M0 0 L5 0\"/>");
        close(qpv::pattern_bounds(styled).width(), 5.0, 1e-6, "SVG styles, inheritance and defs");

        const auto shapes = svg_doc("shapes.svg",
            "<line x1=\"0\" y1=\"0\" x2=\"10\" y2=\"0\" stroke=\"black\"/>"
            "<polyline points=\"0,0 0,10 5,10\" stroke=\"black\" fill=\"none\"/>"
            "<circle cx=\"20\" cy=\"5\" r=\"5\" stroke=\"black\" fill=\"none\"/>");
        check(has_arc(shapes), "SVG circle keeps arcs");
        close(qpv::pattern_bounds(shapes).width(), 25.0, 1e-3, "SVG basic shapes width");
        close(qpv::pattern_bounds(shapes).height(), 10.0, 1e-3, "SVG basic shapes height");

        const auto rotated = svg_doc("rotated.svg",
            "<circle r=\"5\" transform=\"rotate(30)\" stroke=\"black\" fill=\"none\"/>");
        check(has_arc(rotated), "SVG rotated circle keeps arcs");
        const auto stretched = svg_doc("stretched.svg",
            "<circle r=\"5\" transform=\"scale(2,1)\" stroke=\"black\" fill=\"none\"/>");
        check(!has_arc(stretched), "SVG stretched circle is flattened");
        close(qpv::pattern_bounds(stretched).width(), 20.0, 0.01, "SVG stretched circle width");
        close(qpv::pattern_bounds(stretched).height(), 10.0, 0.01, "SVG stretched circle height");

        const auto rounded = svg_doc("rounded.svg",
            "<rect width=\"20\" height=\"10\" rx=\"2\" stroke=\"black\" fill=\"none\"/>");
        check(has_arc(rounded), "SVG rounded rect corners are arcs");
        close(qpv::pattern_bounds(rounded).width(), 20.0, 1e-3, "SVG rounded rect width");

        const auto framed = svg_doc("framed.svg",
            "<rect width=\"100\" height=\"100\" stroke=\"black\" fill=\"none\"/>"
            "<path stroke=\"black\" d=\"M10 10 L20 20\"/>");
        close(qpv::pattern_bounds(framed).width(), 10.0, 1e-6, "SVG registration frame removed");
        const auto inner = svg_doc("inner.svg",
            "<path stroke=\"black\" d=\"M0 0 L100 100\"/>"
            "<rect x=\"10\" y=\"10\" width=\"20\" height=\"20\" stroke=\"black\" fill=\"none\"/>");
        check(stitches(inner) == 5, "SVG square inside a design kept");
        const auto square = svg_doc("square.svg",
            "<rect width=\"10\" height=\"10\" stroke=\"black\" fill=\"none\"/>");
        check(stitches(square) == 4, "SVG design that is only a square kept");

        // Number parsing must not depend on the C locale's decimal separator.
        bool comma_locale = false;
        for (const char* name : {"de-DE", "German_Germany.1252", "de_DE.UTF-8", "de_DE"}) {
            if (std::setlocale(LC_ALL, name) && *std::localeconv()->decimal_point == ',') {
                comma_locale = true;
                break;
            }
        }
        if (comma_locale) {
            const auto decimal_qli = dir / "decimal.qli";
            write(decimal_qli, "VP 1500.5,0\nEN\n");
            close(qpv::read_qli(decimal_qli).moves[1].end.x, 1.5005, 1e-9, "QLI locale");
            const auto decimal_svg = dir / "decimal.svg";
            write(decimal_svg, "<svg width=\"100.5in\" viewBox=\"0 0 100.5 100\">"
                               "<path d=\"M0 0 L10.5 0\" stroke=\"black\"/></svg>");
            close(qpv::pattern_bounds(qpv::read_svg(decimal_svg)).width(), 10.5, 1e-6,
                  "SVG locale");
            std::setlocale(LC_ALL, "C");
        } else {
            std::cout << "Skipped locale test: no comma-decimal locale available\n";
        }
        std::cout << "All core tests passed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Test failure: " << e.what() << '\n';
        return 1;
    }
}
