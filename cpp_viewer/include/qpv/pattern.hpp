#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace qpv {

struct Point {
    double x{};
    double y{};
};

struct Move {
    int sequence{};
    int gcode{};
    Point end{};
    std::optional<Point> center;

    [[nodiscard]] bool is_stitch() const noexcept {
        return gcode == 1 || gcode == 2 || gcode == 3;
    }
};

struct Pattern {
    std::vector<Move> moves;
    std::vector<std::string> comments;
    std::optional<int> end_sequence;
    std::filesystem::path filename;
};

struct Bounds {
    double min_x{};
    double min_y{};
    double max_x{};
    double max_y{};
    bool valid{};

    [[nodiscard]] double width() const noexcept { return valid ? max_x - min_x : 0.0; }
    [[nodiscard]] double height() const noexcept { return valid ? max_y - min_y : 0.0; }
};

[[nodiscard]] double arc_sweep(double start_degrees, double end_degrees, bool ccw) noexcept;
[[nodiscard]] std::vector<Point> sample_arc(
    Point start, const Move& arc, double degrees_per_segment = 2.0);
[[nodiscard]] Bounds pattern_bounds(const Pattern& pattern);

} // namespace qpv
