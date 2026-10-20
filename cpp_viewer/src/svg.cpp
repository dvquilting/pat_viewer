#include "qpv/io.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cmath>
#include <fstream>
#include <numbers>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace qpv {
namespace {

struct Raw {
    enum class Kind { Travel, Stitch, Arc } kind{Kind::Travel};
    Point start{};
    Point end{};
    Point center{};
    int gcode{};
};

struct Cubic { Point p0, p1, p2, p3; };
struct Quad { Point p0, p1, p2; };

Point operator+(Point a, Point b) { return {a.x + b.x, a.y + b.y}; }
Point operator-(Point a, Point b) { return {a.x - b.x, a.y - b.y}; }
Point operator*(Point p, double s) { return {p.x * s, p.y * s}; }
double distance(Point a, Point b) { return std::hypot(a.x - b.x, a.y - b.y); }
Point lerp(Point a, Point b, double t) { return a * (1.0 - t) + b * t; }

std::string lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

bool is_space(char c) { return std::isspace(static_cast<unsigned char>(c)) != 0; }

bool is_name_char(char c) {
    return std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' ||
           c == ':' || c == '.';
}

bool iequals(std::string_view a, std::string_view b) {
    return a.size() == b.size() &&
           std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
               return std::tolower(static_cast<unsigned char>(x)) ==
                      std::tolower(static_cast<unsigned char>(y));
           });
}

std::size_t ifind(std::string_view text, std::string_view needle, std::size_t from) {
    for (std::size_t i = from; i + needle.size() <= text.size(); ++i)
        if (iequals(text.substr(i, needle.size()), needle)) return i;
    return std::string_view::npos;
}

std::string_view trim(std::string_view value) {
    while (!value.empty() && is_space(value.front())) value.remove_prefix(1);
    while (!value.empty() && is_space(value.back())) value.remove_suffix(1);
    return value;
}

// Locale-independent replacement for strtod that also accepts a leading '+'.
std::from_chars_result parse_double(const char* begin, const char* end, double& value) {
    if (begin != end && *begin == '+' && (end - begin < 2 || begin[1] != '-')) ++begin;
    return std::from_chars(begin, end, value);
}

// Finds the next start tag called `name` (e.g. "<path ...>") at or after `from`
// and advances `from` past it. Quoted attribute values may contain '>'.
// Hand-written because libstdc++'s std::regex recurses once per character and
// overflows the stack on path data longer than a few kilobytes.
std::string_view next_tag(std::string_view xml, std::string_view name, std::size_t& from) {
    while (from < xml.size()) {
        const auto open = xml.find('<', from);
        if (open == std::string_view::npos) break;
        from = open + 1;
        const auto after = open + 1 + name.size();
        if (after > xml.size() || !iequals(xml.substr(open + 1, name.size()), name) ||
            (after < xml.size() && is_name_char(xml[after])))
            continue;
        char quote = 0;
        for (auto i = after; i < xml.size(); ++i) {
            const char c = xml[i];
            if (quote) {
                if (c == quote) quote = 0;
            } else if (c == '"' || c == '\'') {
                quote = c;
            } else if (c == '>') {
                from = i + 1;
                return xml.substr(open, i - open + 1);
            }
        }
        break;
    }
    from = xml.size();
    return {};
}

// Returns the value of attribute `name` in a start tag, matching the whole
// attribute name case-insensitively (so "width" never matches "stroke-width").
std::string attribute(std::string_view tag, std::string_view name) {
    std::size_t i = 1;
    while (i < tag.size() && is_name_char(tag[i])) ++i;  // element name
    while (i < tag.size()) {
        while (i < tag.size() && !is_name_char(tag[i])) ++i;
        const auto name_begin = i;
        while (i < tag.size() && is_name_char(tag[i])) ++i;
        const auto attr = tag.substr(name_begin, i - name_begin);
        while (i < tag.size() && is_space(tag[i])) ++i;
        if (i >= tag.size() || tag[i] != '=') continue;
        ++i;
        while (i < tag.size() && is_space(tag[i])) ++i;
        if (i >= tag.size()) break;
        std::string_view value;
        if (tag[i] == '"' || tag[i] == '\'') {
            const auto close = tag.find(tag[i], i + 1);
            if (close == std::string_view::npos) break;
            value = tag.substr(i + 1, close - i - 1);
            i = close + 1;
        } else {
            const auto begin = i;
            while (i < tag.size() && !is_space(tag[i]) && tag[i] != '>') ++i;
            value = tag.substr(begin, i - begin);
        }
        if (iequals(attr, name)) return std::string(value);
    }
    return {};
}

// True when argument `index` of any `function(...)` in the document is negative,
// e.g. the second argument of scale() or the fourth of matrix().
bool has_negative_argument(std::string_view xml, std::string_view function,
                           std::size_t index) {
    for (auto pos = ifind(xml, function, 0); pos != std::string_view::npos;
         pos = ifind(xml, function, pos + 1)) {
        auto i = pos + function.size();
        while (i < xml.size() && is_space(xml[i])) ++i;
        if (i >= xml.size() || xml[i] != '(') continue;
        ++i;
        for (std::size_t arg = 0;; ++arg) {
            while (i < xml.size() && (is_space(xml[i]) || xml[i] == ',')) ++i;
            if (i >= xml.size() || xml[i] == ')') break;
            const auto begin = i;
            while (i < xml.size() && !is_space(xml[i]) && xml[i] != ',' && xml[i] != ')') ++i;
            if (arg == index) {
                if (xml[begin] == '-') return true;
                break;
            }
        }
    }
    return false;
}

double point_line_distance(Point p, Point a, Point b) {
    const Point chord = b - a;
    const double len = std::hypot(chord.x, chord.y);
    if (len < 1e-12) return distance(p, a);
    return std::abs(chord.x * (a.y - p.y) - (a.x - p.x) * chord.y) / len;
}

void flatten(const Cubic& c, double tolerance, int depth, std::vector<Point>& out) {
    const double flatness = std::max(point_line_distance(c.p1, c.p0, c.p3),
                                     point_line_distance(c.p2, c.p0, c.p3));
    if (depth >= 12 || flatness <= tolerance) {
        out.push_back(c.p3);
        return;
    }
    const Point a = lerp(c.p0, c.p1, .5), b = lerp(c.p1, c.p2, .5);
    const Point d = lerp(c.p2, c.p3, .5), e = lerp(a, b, .5);
    const Point f = lerp(b, d, .5), mid = lerp(e, f, .5);
    flatten(Cubic{c.p0, a, e, mid}, tolerance, depth + 1, out);
    flatten(Cubic{mid, f, d, c.p3}, tolerance, depth + 1, out);
}

void flatten(const Quad& q, double tolerance, int depth, std::vector<Point>& out) {
    if (depth >= 12 || point_line_distance(q.p1, q.p0, q.p2) <= tolerance) {
        out.push_back(q.p2);
        return;
    }
    const Point a = lerp(q.p0, q.p1, .5), b = lerp(q.p1, q.p2, .5);
    const Point mid = lerp(a, b, .5);
    flatten(Quad{q.p0, a, mid}, tolerance, depth + 1, out);
    flatten(Quad{mid, b, q.p2}, tolerance, depth + 1, out);
}

class PathTokens {
public:
    explicit PathTokens(std::string_view text) : text_(text) {}

    bool empty() {
        skip();
        return pos_ >= text_.size();
    }
    bool command_ahead() {
        skip();
        return pos_ < text_.size() && std::isalpha(static_cast<unsigned char>(text_[pos_]));
    }
    char command() {
        skip();
        if (!command_ahead()) throw std::runtime_error("Expected SVG path command");
        return text_[pos_++];
    }
    double number() {
        skip();
        if (pos_ >= text_.size()) throw std::runtime_error("Missing SVG path number");
        double value{};
        const auto result =
            parse_double(text_.data() + pos_, text_.data() + text_.size(), value);
        if (result.ec != std::errc{} || !std::isfinite(value))
            throw std::runtime_error("Invalid SVG path number");
        pos_ = static_cast<std::size_t>(result.ptr - text_.data());
        return value;
    }
    // Arc flags are single characters, so "0110" is two flags followed by 10.
    bool flag() {
        skip();
        if (pos_ < text_.size() && (text_[pos_] == '0' || text_[pos_] == '1'))
            return text_[pos_++] == '1';
        throw std::runtime_error("Invalid SVG arc flag");
    }

private:
    std::string_view text_;
    std::size_t pos_{};
    void skip() {
        while (pos_ < text_.size() &&
               (std::isspace(static_cast<unsigned char>(text_[pos_])) || text_[pos_] == ','))
            ++pos_;
    }
};

struct ArcGeometry {
    Point center{};
    double rx{};
    double ry{};
    double rotation{};
    double start{};
    double sweep{};
};

std::optional<ArcGeometry> endpoint_arc(Point p0, Point p1, double rx, double ry,
                                        double rotation_deg, bool large, bool sweep) {
    rx = std::abs(rx); ry = std::abs(ry);
    if (rx < 1e-12 || ry < 1e-12 || distance(p0, p1) < 1e-12) return std::nullopt;
    const double phi = rotation_deg * std::numbers::pi / 180.0;
    const double cs = std::cos(phi), sn = std::sin(phi);
    const double dx = (p0.x - p1.x) / 2.0, dy = (p0.y - p1.y) / 2.0;
    const double xp = cs * dx + sn * dy, yp = -sn * dx + cs * dy;
    double lambda = xp * xp / (rx * rx) + yp * yp / (ry * ry);
    if (lambda > 1.0) {
        const double factor = std::sqrt(lambda);
        rx *= factor; ry *= factor;
    }
    const double numerator = std::max(0.0, rx * rx * ry * ry -
        rx * rx * yp * yp - ry * ry * xp * xp);
    const double denominator = rx * rx * yp * yp + ry * ry * xp * xp;
    double factor = denominator > 0.0 ? std::sqrt(numerator / denominator) : 0.0;
    if (large == sweep) factor = -factor;
    const double cxp = factor * rx * yp / ry;
    const double cyp = factor * -ry * xp / rx;
    Point center{cs * cxp - sn * cyp + (p0.x + p1.x) / 2.0,
                 sn * cxp + cs * cyp + (p0.y + p1.y) / 2.0};

    auto angle = [](double ux, double uy, double vx, double vy) {
        return std::atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    };
    const double ux = (xp - cxp) / rx, uy = (yp - cyp) / ry;
    const double vx = (-xp - cxp) / rx, vy = (-yp - cyp) / ry;
    const double start = angle(1.0, 0.0, ux, uy);
    double delta = angle(ux, uy, vx, vy);
    if (!sweep && delta > 0) delta -= 2 * std::numbers::pi;
    if (sweep && delta < 0) delta += 2 * std::numbers::pi;
    return ArcGeometry{center, rx, ry, phi, start, delta};
}

Point arc_point(const ArcGeometry& a, double t) {
    const double theta = a.start + a.sweep * t;
    const double x = a.rx * std::cos(theta), y = a.ry * std::sin(theta);
    return {a.center.x + std::cos(a.rotation) * x - std::sin(a.rotation) * y,
            a.center.y + std::sin(a.rotation) * x + std::cos(a.rotation) * y};
}

void parse_path(const std::string& data, double tolerance, std::vector<Raw>& raw) {
    PathTokens tokens(data);
    Point current{}, subpath{}, last_cubic{}, last_quad{};
    char cmd = 0, previous = 0;
    auto point = [&](bool relative) {
        Point p{tokens.number(), tokens.number()};
        return relative ? current + p : p;
    };
    auto stitch = [&](Point to) {
        if (distance(current, to) > 1e-12) raw.push_back({Raw::Kind::Stitch, current, to});
        current = to;
    };

    while (!tokens.empty()) {
        if (tokens.command_ahead()) cmd = tokens.command();
        else if (!cmd) throw std::runtime_error("SVG path begins without a command");
        const bool relative = std::islower(static_cast<unsigned char>(cmd));
        const char op = static_cast<char>(std::toupper(static_cast<unsigned char>(cmd)));
        if (op == 'Z') {
            stitch(subpath);
            previous = op;
            cmd = 0;
            continue;
        }
        if (op == 'M') {
            current = point(relative);
            subpath = current;
            raw.push_back({Raw::Kind::Travel, {}, current});
            cmd = relative ? 'l' : 'L';
            previous = 'M';
            continue;
        }
        if (op == 'L') stitch(point(relative));
        else if (op == 'H') stitch({relative ? current.x + tokens.number() : tokens.number(), current.y});
        else if (op == 'V') stitch({current.x, relative ? current.y + tokens.number() : tokens.number()});
        else if (op == 'C') {
            Point c1 = point(relative), c2 = point(relative), end = point(relative);
            std::vector<Point> points;
            flatten(Cubic{current, c1, c2, end}, tolerance, 0, points);
            for (Point p : points) stitch(p);
            last_cubic = c2;
        } else if (op == 'S') {
            Point c1 = previous == 'C' || previous == 'S' ? current * 2.0 - last_cubic : current;
            Point c2 = point(relative), end = point(relative);
            std::vector<Point> points;
            flatten(Cubic{current, c1, c2, end}, tolerance, 0, points);
            for (Point p : points) stitch(p);
            last_cubic = c2;
        } else if (op == 'Q') {
            Point control = point(relative), end = point(relative);
            std::vector<Point> points;
            flatten(Quad{current, control, end}, tolerance, 0, points);
            for (Point p : points) stitch(p);
            last_quad = control;
        } else if (op == 'T') {
            Point control = previous == 'Q' || previous == 'T' ? current * 2.0 - last_quad : current;
            Point end = point(relative);
            std::vector<Point> points;
            flatten(Quad{current, control, end}, tolerance, 0, points);
            for (Point p : points) stitch(p);
            last_quad = control;
        } else if (op == 'A') {
            const double rx = tokens.number(), ry = tokens.number();
            const double rotation = tokens.number();
            const bool large = tokens.flag(), sweep = tokens.flag();
            const Point end = point(relative);
            const auto arc = endpoint_arc(current, end, rx, ry, rotation, large, sweep);
            if (!arc) stitch(end);
            else if (std::abs(arc->rx - arc->ry) <= 0.02 * std::max(arc->rx, arc->ry)) {
                raw.push_back({Raw::Kind::Arc, current, end, arc->center, sweep ? 2 : 3});
                current = end;
            } else {
                const int segments = std::max(16, static_cast<int>(std::ceil(std::abs(arc->sweep) * 8.0)));
                for (int i = 1; i <= segments; ++i)
                    stitch(i == segments ? end : arc_point(*arc, static_cast<double>(i) / segments));
            }
        } else {
            throw std::runtime_error(std::string("Unsupported SVG path command: ") + op);
        }
        previous = op;
    }
}

double dimension_px(std::string_view text) {
    text = trim(text);
    double value{};
    const auto result = parse_double(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || !std::isfinite(value)) return 0.0;
    const auto unit = lower(std::string(trim(
        text.substr(static_cast<std::size_t>(result.ptr - text.data())))));
    if (unit == "pt") return value * 96.0 / 72.0;
    if (unit == "mm") return value * 96.0 / 25.4;
    if (unit == "cm") return value * 96.0 / 2.54;
    if (unit == "in") return value * 96.0;
    if (unit.empty() || unit == "px") return value;
    return 0.0;
}

bool decorative(std::string_view tag) {
    static const std::array<std::string_view, 18> colors{
        "red","green","blue","yellow","cyan","magenta","lime","fuchsia",
        "aqua","orange","purple","gray","grey","silver","maroon","navy","teal","olive"};
    const auto stroke = lower(attribute(tag, "stroke"));
    const auto fill = lower(attribute(tag, "fill"));
    if (stroke == "none") return true;
    if (!fill.empty() && fill != "none" && stroke.empty()) return true;
    return std::find(colors.begin(), colors.end(), stroke) != colors.end();
}

double rounded4(double v) { return std::round(v * 10000.0) / 10000.0; }

} // namespace

Pattern read_svg(const std::filesystem::path& path, double bezier_tolerance) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("Cannot open SVG file: " + path.string());
    const std::string xml((std::istreambuf_iterator<char>(in)), {});

    std::size_t cursor = 0;
    const std::string_view root = next_tag(xml, "svg", cursor);
    const double width_px = dimension_px(attribute(root, "width"));
    const double height_px = dimension_px(attribute(root, "height"));
    double vb_w = 0.0, vb_h = 0.0;
    {
        const auto vb = attribute(root, "viewBox");
        PathTokens tokens(vb);
        try {
            tokens.number();
            tokens.number();
            vb_w = tokens.number();
            vb_h = tokens.number();
        } catch (const std::runtime_error&) {
            // Missing or malformed viewBox: keep whatever was parsed.
        }
    }
    std::optional<double> svg_scale;
    if (width_px > 0.0 && vb_w > 0.0) svg_scale = width_px / vb_w / 96.0;
    else if (height_px > 0.0 && vb_h > 0.0) svg_scale = height_px / vb_h / 96.0;

    const bool y_up = has_negative_argument(xml, "matrix", 3) ||
                      has_negative_argument(xml, "scale", 1);

    std::vector<Raw> raw;
    raw.reserve(4096);
    cursor = 0;
    for (std::string_view tag = next_tag(xml, "path", cursor); !tag.empty();
         tag = next_tag(xml, "path", cursor)) {
        if (decorative(tag)) continue;
        const std::string d = attribute(tag, "d");
        if (!d.empty()) {
            const auto first = raw.size();
            parse_path(d, bezier_tolerance / svg_scale.value_or(1.0), raw);
            // Closed four-sided axis-aligned paths are registration frames.
            if (raw.size() == first + 5 &&
                raw[first].kind == Raw::Kind::Travel) {
                bool rectangle = true;
                for (std::size_t n = first + 1; n < raw.size(); ++n) {
                    const auto& r = raw[n];
                    rectangle = rectangle && r.kind == Raw::Kind::Stitch &&
                        (std::abs(r.end.x - r.start.x) < .01 ||
                         std::abs(r.end.y - r.start.y) < .01);
                }
                rectangle = rectangle &&
                    distance(raw[first].end, raw.back().end) < .01;
                if (rectangle) raw.resize(first);
            }
        }
    }

    Pattern result;
    result.filename = path;
    if (raw.empty()) return result;
    double min_x = raw.front().end.x, max_x = min_x;
    double min_y = raw.front().end.y, max_y = min_y;
    auto include = [&](Point p) {
        min_x = std::min(min_x, p.x); max_x = std::max(max_x, p.x);
        min_y = std::min(min_y, p.y); max_y = std::max(max_y, p.y);
    };
    for (const auto& r : raw) {
        if (r.kind != Raw::Kind::Travel) include(r.start);
        include(r.end);
        if (r.kind == Raw::Kind::Arc) {
            const double radius = distance(r.start, r.center);
            include({r.center.x - radius, r.center.y - radius});
            include({r.center.x + radius, r.center.y + radius});
        }
    }
    if (!svg_scale) {
        const double longest = std::max(max_x - min_x, max_y - min_y);
        svg_scale = longest > 0.0 ? 12.0 / longest : 1.0;
    }
    auto transform = [&](Point p) {
        return Point{rounded4((p.x - min_x) * *svg_scale),
                     rounded4(((y_up ? p.y - min_y : max_y - p.y)) * *svg_scale)};
    };

    result.moves.reserve(raw.size() + 1);
    int sequence = 10;
    result.moves.push_back({sequence++, 0, {0.0, 0.0}, std::nullopt});
    for (const auto& r : raw) {
        if (r.kind == Raw::Kind::Travel) {
            result.moves.push_back({sequence++, 0, transform(r.end), std::nullopt});
        } else if (distance(transform(r.start), transform(r.end)) > 1e-6) {
            if (r.kind == Raw::Kind::Arc) {
                int gcode = r.gcode;
                if (y_up) gcode = gcode == 2 ? 3 : 2;
                result.moves.push_back({sequence++, gcode, transform(r.end), transform(r.center)});
            } else {
                result.moves.push_back({sequence++, 1, transform(r.end), std::nullopt});
            }
        }
    }
    result.end_sequence = sequence;
    return result;
}

} // namespace qpv
