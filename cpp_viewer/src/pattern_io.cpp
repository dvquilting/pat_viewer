#include "qpv/io.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <limits>
#include <numbers>
#include <sstream>
#include <stdexcept>
#include <string_view>

namespace qpv {
namespace {

void include_point(Bounds& b, Point p) {
    if (!b.valid) {
        b = {p.x, p.y, p.x, p.y, true};
        return;
    }
    b.min_x = std::min(b.min_x, p.x);
    b.min_y = std::min(b.min_y, p.y);
    b.max_x = std::max(b.max_x, p.x);
    b.max_y = std::max(b.max_y, p.y);
}

std::string_view trim(std::string_view value) {
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front())))
        value.remove_prefix(1);
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back())))
        value.remove_suffix(1);
    return value;
}

std::size_t find_tag(std::string_view line, char tag, std::size_t from) {
    for (std::size_t i = from; i < line.size(); ++i)
        if (std::toupper(static_cast<unsigned char>(line[i])) == tag) return i;
    return std::string_view::npos;
}

bool parse_integer(std::string_view line, char tag, std::size_t& cursor, int& value) {
    const auto tag_pos = find_tag(line, tag, cursor);
    if (tag_pos == std::string_view::npos) return false;
    const char* begin = line.data() + tag_pos + 1;
    const char* end = line.data() + line.size();
    while (begin != end && std::isspace(static_cast<unsigned char>(*begin))) ++begin;
    const auto result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{}) return false;
    cursor = static_cast<std::size_t>(result.ptr - line.data());
    return true;
}

bool parse_number(std::string_view line, char tag, std::size_t& cursor, double& value) {
    const auto tag_pos = find_tag(line, tag, cursor);
    if (tag_pos == std::string_view::npos) return false;
    const char* begin = line.data() + tag_pos + 1;
    const char* end = line.data() + line.size();
    while (begin != end && std::isspace(static_cast<unsigned char>(*begin))) ++begin;
    if (begin != end && *begin == '+') ++begin;
    const auto result = std::from_chars(begin, end, value);
    if (result.ec != std::errc{}) return false;
    cursor = static_cast<std::size_t>(result.ptr - line.data());
    return true;
}

} // namespace

std::string lower_extension(const std::filesystem::path& path) {
    auto ext = path.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return ext;
}

bool is_supported_file(const std::filesystem::path& path) {
    const auto ext = lower_extension(path);
    return ext == ".pat" || ext == ".qli" || ext == ".svg";
}

double arc_sweep(double start, double end, bool ccw) noexcept {
    auto positive_mod = [](double v) {
        v = std::fmod(v, 360.0);
        return v < 0.0 ? v + 360.0 : v;
    };
    double sweep = ccw ? positive_mod(end - start) : -positive_mod(start - end);
    if (std::abs(sweep) < 1e-12) sweep = ccw ? 180.0 : -180.0;
    return sweep;
}

std::vector<Point> sample_arc(Point start, const Move& arc, double step) {
    if (!arc.center || (arc.gcode != 2 && arc.gcode != 3)) return {start, arc.end};
    const Point c = *arc.center;
    const double radius = std::hypot(start.x - c.x, start.y - c.y);
    if (radius < 1e-9) return {start, arc.end};
    const double a0 = std::atan2(start.y - c.y, start.x - c.x);
    const double a1 = std::atan2(arc.end.y - c.y, arc.end.x - c.x);
    const double sweep_deg = arc_sweep(a0 * 180.0 / std::numbers::pi,
                                       a1 * 180.0 / std::numbers::pi,
                                       arc.gcode == 3);
    const int count = std::max(4, static_cast<int>(std::abs(sweep_deg) / step) + 1);
    const double sweep = sweep_deg * std::numbers::pi / 180.0;
    std::vector<Point> points;
    points.reserve(static_cast<std::size_t>(count) + 1);
    points.push_back(start);
    for (int i = 1; i < count; ++i) {
        const double angle = a0 + sweep * (static_cast<double>(i) / count);
        points.push_back({c.x + radius * std::cos(angle), c.y + radius * std::sin(angle)});
    }
    points.push_back(arc.end);
    return points;
}

Bounds pattern_bounds(const Pattern& pattern) {
    Bounds result;
    Point current{};
    bool have_current = false;
    for (const auto& move : pattern.moves) {
        if (have_current && move.center && (move.gcode == 2 || move.gcode == 3)) {
            for (const auto& p : sample_arc(current, move, 2.0)) include_point(result, p);
        } else {
            include_point(result, move.end);
        }
        current = move.end;
        have_current = true;
    }
    return result;
}

Pattern read_pat(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot open PAT file: " + path.string());

    Pattern pattern;
    pattern.filename = path;
    std::string line;
    while (std::getline(input, line)) {
        const std::string_view stripped = trim(line);
        if (stripped.size() >= 2 && stripped.front() == '(' && stripped.back() == ')') {
            pattern.comments.emplace_back(trim(stripped.substr(1, stripped.size() - 2)));
            continue;
        }

        std::size_t cursor = 0;
        int sequence = 0;
        if (!parse_integer(line, 'N', cursor, sequence)) continue;

        std::size_t end_cursor = cursor;
        int end_code = 0;
        if (parse_integer(line, 'M', end_cursor, end_code) && end_code == 2) {
            pattern.end_sequence = sequence;
            continue;
        }

        Move move;
        move.sequence = sequence;
        if (!parse_integer(line, 'G', cursor, move.gcode) ||
            !parse_number(line, 'X', cursor, move.end.x) ||
            !parse_number(line, 'Y', cursor, move.end.y)) continue;

        std::size_t arc_cursor = cursor;
        Point center;
        if (parse_number(line, 'I', arc_cursor, center.x) &&
            parse_number(line, 'J', arc_cursor, center.y))
            move.center = center;
        pattern.moves.push_back(move);
    }
    return pattern;
}

void write_pat(const Pattern& pattern, const std::filesystem::path& path) {
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("Cannot write PAT file: " + path.string());
    out << std::fixed << std::setprecision(4);
    for (const auto& move : pattern.moves) {
        if (move.gcode == 0 || move.gcode == 1) {
            out << 'N' << std::setw(2) << std::setfill('0') << move.sequence
                << 'G' << std::setw(2) << move.gcode
                << 'X' << move.end.x << 'Y' << move.end.y << "\r\n";
        } else if ((move.gcode == 2 || move.gcode == 3) && move.center) {
            out << 'N' << std::setw(2) << std::setfill('0') << move.sequence
                << 'G' << std::setw(2) << move.gcode
                << 'X' << move.end.x << 'Y' << move.end.y
                << 'I' << move.center->x << 'J' << move.center->y << "\r\n";
        }
    }
    if (pattern.end_sequence) {
        out << 'N' << std::setw(2) << std::setfill('0') << *pattern.end_sequence
            << "M02\r\n";
    }
    for (const auto& comment : pattern.comments) out << "( " << comment << " )\r\n";
    if (!out) throw std::runtime_error("Error writing PAT file: " + path.string());
}

Pattern load_pattern(const std::filesystem::path& path, double tolerance) {
    const auto ext = lower_extension(path);
    if (ext == ".pat") return read_pat(path);
    if (ext == ".qli") return read_qli(path);
    if (ext == ".svg") return read_svg(path, tolerance);
    throw std::runtime_error("Unsupported input format: " + path.string());
}

void convert_file(const std::filesystem::path& input,
                  const std::filesystem::path& output,
                  double tolerance) {
    Pattern pattern = load_pattern(input, tolerance);
    const auto ext = lower_extension(output);
    if (ext == ".pat") write_pat(pattern, output);
    else if (ext == ".qli") write_qli(pattern, output);
    else throw std::runtime_error("Output must have .pat or .qli extension");
}

} // namespace qpv
