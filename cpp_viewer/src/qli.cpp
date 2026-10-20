#include "qpv/io.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <numbers>
#include <sstream>
#include <stdexcept>

namespace qpv {
namespace {

constexpr double scale = 0.001;

std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::string upper(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return value;
}

double number(std::string token) {
    token = trim(std::move(token));
    if (token.rfind("@RND[", 0) == 0) token = trim(token.substr(5));
    // Search from 1 so that a leading sign is kept.
    const auto junk = token.find_first_of("]\r\n*+<>", 1);
    if (junk != std::string::npos) token.resize(junk);
    // std::from_chars ignores the C locale, unlike std::stod.
    const char* begin = token.data();
    const char* end = begin + token.size();
    if (begin != end && *begin == '+') ++begin;
    double value{};
    const auto result = std::from_chars(begin, end, value);
    return result.ec == std::errc{} && std::isfinite(value) ? value : 0.0;
}

std::vector<std::string> arguments(std::string_view text) {
    std::vector<std::string> result;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        const auto comma = text.find(',', begin);
        result.emplace_back(trim(std::string(text.substr(
            begin, comma == std::string_view::npos ? text.size() - begin : comma - begin))));
        if (comma == std::string_view::npos) break;
        begin = comma + 1;
    }
    return result;
}

std::string format_number(double value) {
    const auto rounded = std::round(value);
    if (std::abs(value - rounded) < 1e-6) return std::to_string(static_cast<long long>(rounded));
    std::ostringstream out;
    out << std::fixed << std::setprecision(4) << value;
    auto text = out.str();
    while (!text.empty() && text.back() == '0') text.pop_back();
    if (!text.empty() && text.back() == '.') text.pop_back();
    return text;
}

class Reader {
public:
    Pattern read(const std::filesystem::path& path) {
        std::ifstream in(path);
        if (!in) throw std::runtime_error("Cannot open QLI file: " + path_text(path));
        result_.filename = path;
        result_.moves.reserve(4096);
        result_.moves.push_back({10, 0, {0.0, 0.0}, std::nullopt});
        std::string line;
        while (std::getline(in, line)) {
            std::size_t begin = 0;
            while (begin <= line.size()) {
                const auto semi = line.find(';', begin);
                parse_command(trim(line.substr(begin,
                    semi == std::string::npos ? std::string::npos : semi - begin)));
                if (semi == std::string::npos) break;
                begin = semi + 1;
            }
        }
        result_.end_sequence = sequence_;
        return std::move(result_);
    }

private:
    Pattern result_;
    double relative_x_{};
    double relative_y_{};
    double current_x_{};
    double current_y_{};
    int needle_{1};
    int sequence_{11};

    void linear(double x, double y) {
        result_.moves.push_back({sequence_++, needle_, {x * scale, y * scale}, std::nullopt});
        current_x_ = x;
        current_y_ = y;
    }

    void parse_command(const std::string& original) {
        if (original.empty()) return;
        const auto text = upper(original);
        if (text.rfind("NO", 0) == 0) {
            if (text.find("NEEDLE OFF") != std::string::npos) needle_ = 0;
            else if (text.find("NEEDLE ON") != std::string::npos) needle_ = 1;
            else if (const auto pos = text.find("INFO"); pos != std::string::npos)
                result_.comments.push_back(trim(original.substr(pos + 4)));
            return;
        }
        if (text.rfind("REM", 0) == 0) {
            if (text.find("FIRST_MACRO") == std::string::npos &&
                text.find("END MACRO") == std::string::npos &&
                text.find("CONTINUOUS") == std::string::npos)
                result_.comments.push_back(trim(original.substr(3)));
            return;
        }
        if (text.rfind("CS", 0) == 0) {
            current_x_ = current_y_ = 0.0;
            return;
        }
        if (text.rfind("VE", 0) == 0) {
            relative_x_ = current_x_;
            relative_y_ = current_y_;
            return;
        }
        if (text.rfind("VP", 0) == 0) {
            auto args = arguments(original.substr(2));
            if (args.size() >= 2)
                linear(relative_x_ + number(args[0]), relative_y_ + number(args[1]));
            return;
        }
        if (text.rfind("CR", 0) == 0) {
            auto args = arguments(original.substr(2));
            if (args.size() < 3) return;
            const double radius = number(args[0]);
            const double start = number(args[1]) * std::numbers::pi / 180.0;
            const double range = number(args[2]);
            const double end = start + range * std::numbers::pi / 180.0;
            const double sx = radius * std::cos(start);
            const double sy = radius * std::sin(start);
            const double nx = current_x_ + radius * std::cos(end) - sx;
            const double ny = current_y_ + radius * std::sin(end) - sy;
            if (needle_ == 0) {
                // With the needle off nothing is sewn, so this is a travel move;
                // PAT travel moves are straight.
                result_.moves.push_back({sequence_++, 0, {nx * scale, ny * scale}, std::nullopt});
            } else {
                // A PAT arc whose end meets its start is sewn as a semicircle,
                // so a full circle must not be written as one arc. Arcs over
                // 180 degrees are split into equal pieces of at most 180 (a
                // split 270-degree arc sews the same).
                const double cx = current_x_ - sx, cy = current_y_ - sy;
                const int pieces =
                    std::max(1, static_cast<int>(std::ceil(std::abs(range) / 180.0 - 1e-9)));
                const double step = range * std::numbers::pi / 180.0 / pieces;
                for (int k = 1; k <= pieces; ++k) {
                    const double px = k == pieces ? nx : cx + radius * std::cos(start + step * k);
                    const double py = k == pieces ? ny : cy + radius * std::sin(start + step * k);
                    result_.moves.push_back({sequence_++, range < 0 ? 2 : 3,
                                             {px * scale, py * scale},
                                             Point{cx * scale, cy * scale}});
                }
            }
            current_x_ = nx;
            current_y_ = ny;
        }
    }
};

} // namespace

Pattern read_qli(const std::filesystem::path& path) {
    return Reader{}.read(path);
}

void write_qli(const Pattern& pattern, const std::filesystem::path& path) {
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("Cannot write QLI file: " + path_text(path));
    for (auto comment : pattern.comments) {
        // ';' separates QLI commands, so it would turn the rest of the comment
        // into commands when the file is read back.
        std::replace_if(comment.begin(), comment.end(),
                        [](char c) { return c == '\r' || c == '\n' || c == ';'; }, ' ');
        comment = trim(std::move(comment));
        if (!comment.empty()) out << "REM " << comment << "\r\n";
    }

    int needle = 1;
    Point current{};
    for (const auto& move : pattern.moves) {
        if (move.gcode == 0 || move.gcode == 1) {
            const int wanted = move.gcode;
            if (wanted != needle) {
                out << (wanted ? "NO NEEDLE ON\r\n" : "NO NEEDLE OFF\r\n");
                needle = wanted;
            }
            out << "VP " << format_number(move.end.x / scale) << ','
                << format_number(move.end.y / scale) << "\r\n";
        } else if ((move.gcode == 2 || move.gcode == 3) && move.center) {
            if (!needle) { out << "NO NEEDLE ON\r\n"; needle = 1; }
            const double radius = std::hypot(current.x - move.center->x,
                                             current.y - move.center->y);
            if (radius < 1e-12) {
                out << "VP " << format_number(move.end.x / scale) << ','
                    << format_number(move.end.y / scale) << "\r\n";
            } else {
                const double a0 = std::atan2(current.y - move.center->y,
                                             current.x - move.center->x) *
                                  180.0 / std::numbers::pi;
                const double a1 = std::atan2(move.end.y - move.center->y,
                                             move.end.x - move.center->x) *
                                  180.0 / std::numbers::pi;
                out << "CR " << format_number(radius / scale) << ','
                    << format_number(a0) << ','
                    << format_number(arc_sweep(a0, a1, move.gcode == 3)) << "\r\n";
            }
        } else {
            if (!needle) { out << "NO NEEDLE ON\r\n"; needle = 1; }
            out << "VP " << format_number(move.end.x / scale) << ','
                << format_number(move.end.y / scale) << "\r\n";
        }
        current = move.end;
    }
    out << "EN\r\n";
    if (!out) throw std::runtime_error("Error writing QLI file: " + path_text(path));
}

} // namespace qpv
