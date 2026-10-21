#include "qpv/io.hpp"
#include "qpv/parse_double.hpp"

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
    // Arcs only: sweeps towards increasing angle in SVG coordinates (y down),
    // which looks clockwise on screen.
    bool positive{};
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

std::string_view trim(std::string_view value) {
    while (!value.empty() && is_space(value.front())) value.remove_prefix(1);
    while (!value.empty() && is_space(value.back())) value.remove_suffix(1);
    return value;
}

// A start or end tag. `name` has any namespace prefix removed ("svg:path" is
// "path").
struct Tag {
    std::string_view text;
    std::string_view name;
    bool closing{};       // </name>
    bool self_closing{};  // <name ... />
};

// Returns the next start or end tag at or after `pos` and advances `pos` past
// it, skipping comments, CDATA, processing instructions and declarations;
// `text` is empty at the end. Quoted attribute values may contain '>'.
// Hand-written because libstdc++'s std::regex recurses once per character and
// overflows the stack on path data longer than a few kilobytes.
Tag next_element(std::string_view xml, std::size_t& pos) {
    auto skip_past = [&](std::size_t from, std::string_view terminator) {
        const auto end = xml.find(terminator, from);
        pos = end == std::string_view::npos ? xml.size() : end + terminator.size();
    };
    while (pos < xml.size()) {
        const auto open = xml.find('<', pos);
        if (open == std::string_view::npos) break;
        const auto rest = xml.substr(open);
        if (rest.starts_with("<!--")) { skip_past(open + 4, "-->"); continue; }
        if (rest.starts_with("<![CDATA[")) { skip_past(open + 9, "]]>"); continue; }
        if (rest.size() > 1 && (rest[1] == '?' || rest[1] == '!')) { skip_past(open, ">"); continue; }
        Tag tag;
        tag.closing = rest.size() > 1 && rest[1] == '/';
        auto i = open + (tag.closing ? 2 : 1);
        const auto name_begin = i;
        while (i < xml.size() && is_name_char(xml[i])) ++i;
        tag.name = xml.substr(name_begin, i - name_begin);
        if (const auto colon = tag.name.rfind(':'); colon != std::string_view::npos)
            tag.name.remove_prefix(colon + 1);
        char quote = 0;
        for (; i < xml.size(); ++i) {
            const char c = xml[i];
            if (quote) {
                if (c == quote) quote = 0;
            } else if (c == '"' || c == '\'') {
                quote = c;
            } else if (c == '>') {
                break;
            }
        }
        if (i >= xml.size()) break;
        tag.text = xml.substr(open, i - open + 1);
        auto before = i;
        while (before > open && is_space(xml[before - 1])) --before;
        tag.self_closing = !tag.closing && xml[before - 1] == '/';
        pos = i + 1;
        return tag;
    }
    pos = xml.size();
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

// A presentation property such as stroke or display. A declaration in the
// style attribute wins over the attribute of the same name.
std::string property(std::string_view tag, std::string_view name) {
    const std::string style = attribute(tag, "style");
    std::string_view rest = style;
    while (!rest.empty()) {
        const auto semicolon = rest.find(';');
        const auto declaration = rest.substr(0, semicolon);
        rest = semicolon == std::string_view::npos ? std::string_view{} : rest.substr(semicolon + 1);
        const auto colon = declaration.find(':');
        if (colon != std::string_view::npos && iequals(trim(declaration.substr(0, colon)), name))
            return std::string(trim(declaration.substr(colon + 1)));
    }
    return attribute(tag, name);
}

// An SVG transform matrix(a b c d e f): x' = a x + c y + e, y' = b x + d y + f.
struct Affine {
    double a = 1, b = 0, c = 0, d = 1, e = 0, f = 0;

    [[nodiscard]] Point map(Point p) const { return {a * p.x + c * p.y + e, b * p.x + d * p.y + f}; }
    [[nodiscard]] double det() const { return a * d - b * c; }
    [[nodiscard]] double scale() const { return std::sqrt(std::abs(det())); }
    [[nodiscard]] bool identity() const {
        return a == 1 && b == 0 && c == 0 && d == 1 && e == 0 && f == 0;
    }
    // Rotation, uniform scale, translation and reflection keep circles circular.
    [[nodiscard]] bool similarity() const {
        const double size = a * a + b * b + c * c + d * d;
        return std::abs(a * a + b * b - c * c - d * d) <= 1e-9 * size &&
               std::abs(a * c + b * d) <= 1e-9 * size;
    }
};

// m * n applies n first, then m.
Affine operator*(const Affine& m, const Affine& n) {
    return {m.a * n.a + m.c * n.b, m.b * n.a + m.d * n.b,
            m.a * n.c + m.c * n.d, m.b * n.c + m.d * n.d,
            m.a * n.e + m.c * n.f + m.e, m.b * n.e + m.d * n.f + m.f};
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
                raw.push_back({Raw::Kind::Arc, current, end, arc->center, sweep});
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

// Shapes that are not stitch lines: unstroked, filled without a stroke, or
// stroked in a named colour (registration and guide marks). `stroke` and `fill`
// are the effective values, inherited from enclosing groups.
bool decorative(const std::string& stroke, const std::string& fill) {
    static const std::array<std::string_view, 18> colors{
        "red","green","blue","yellow","cyan","magenta","lime","fuchsia",
        "aqua","orange","purple","gray","grey","silver","maroon","navy","teal","olive"};
    if (stroke == "none") return true;
    if (!fill.empty() && fill != "none" && stroke.empty()) return true;
    return std::find(colors.begin(), colors.end(), stroke) != colors.end();
}

double rounded4(double v) { return std::round(v * 10000.0) / 10000.0; }

// Parses a transform attribute such as "translate(10 5) rotate(30)". Anything
// it cannot read ends the list.
Affine parse_transform(std::string_view text) {
    Affine result;
    std::size_t pos = 0;
    while (pos < text.size()) {
        while (pos < text.size() && (is_space(text[pos]) || text[pos] == ',')) ++pos;
        const auto name_begin = pos;
        while (pos < text.size() && std::isalpha(static_cast<unsigned char>(text[pos]))) ++pos;
        const auto name = text.substr(name_begin, pos - name_begin);
        const auto open = text.find('(', pos);
        const auto close = text.find(')', pos);
        if (name.empty() || open == std::string_view::npos || close == std::string_view::npos ||
            close < open)
            break;
        std::vector<double> args;
        PathTokens tokens(text.substr(open + 1, close - open - 1));
        try {
            while (!tokens.empty()) args.push_back(tokens.number());
        } catch (const std::runtime_error&) {
            break;
        }
        pos = close + 1;
        auto arg = [&args](std::size_t i, double fallback) {
            return i < args.size() ? args[i] : fallback;
        };
        const double radians = arg(0, 0.0) * std::numbers::pi / 180.0;
        Affine m;
        if (iequals(name, "matrix") && args.size() == 6) {
            m = {args[0], args[1], args[2], args[3], args[4], args[5]};
        } else if (iequals(name, "translate") && !args.empty()) {
            m.e = args[0];
            m.f = arg(1, 0.0);
        } else if (iequals(name, "scale") && !args.empty()) {
            m.a = args[0];
            m.d = arg(1, args[0]);
        } else if (iequals(name, "rotate") && !args.empty()) {
            const double cs = std::cos(radians), sn = std::sin(radians);
            const double cx = arg(1, 0.0), cy = arg(2, 0.0);
            m = Affine{1, 0, 0, 1, cx, cy} * Affine{cs, sn, -sn, cs, 0, 0} *
                Affine{1, 0, 0, 1, -cx, -cy};
        } else if (iequals(name, "skewX") && !args.empty()) {
            m.c = std::tan(radians);
        } else if (iequals(name, "skewY") && !args.empty()) {
            m.b = std::tan(radians);
        } else {
            break;
        }
        result = result * m;
    }
    return result;
}

// Locale-independent number formatting for generated path data.
std::string number_text(double value) {
    char buffer[64];
    const auto result = std::to_chars(buffer, buffer + sizeof buffer, value);
    return std::string(buffer, result.ptr);
}

// A length attribute in user units; units such as mm are converted.
double length(std::string_view tag, std::string_view name) {
    return dimension_px(attribute(tag, name));
}

// Path data equivalent to a basic shape element, or empty for other elements.
std::string shape_path(std::string_view name, std::string_view tag) {
    std::string d;
    auto add = [&d](std::initializer_list<std::string_view> parts) {
        for (const auto part : parts) d += part;
    };
    auto n = [](double v) { return number_text(v); };
    if (iequals(name, "path")) return attribute(tag, "d");
    if (iequals(name, "line")) {
        add({"M", n(length(tag, "x1")), " ", n(length(tag, "y1")),
             "L", n(length(tag, "x2")), " ", n(length(tag, "y2"))});
    } else if (iequals(name, "polyline") || iequals(name, "polygon")) {
        const auto points = attribute(tag, "points");
        if (trim(points).empty()) return {};
        // "M x y x y ..." continues with implicit line-tos.
        d = "M" + points;
        if (iequals(name, "polygon")) d += "Z";
    } else if (iequals(name, "rect")) {
        const double x = length(tag, "x"), y = length(tag, "y");
        const double w = length(tag, "width"), h = length(tag, "height");
        if (w <= 0 || h <= 0) return {};
        double rx = length(tag, "rx"), ry = length(tag, "ry");
        if (attribute(tag, "rx").empty()) rx = ry;
        if (attribute(tag, "ry").empty()) ry = rx;
        rx = std::clamp(rx, 0.0, w / 2);
        ry = std::clamp(ry, 0.0, h / 2);
        if (rx <= 0 || ry <= 0) {
            add({"M", n(x), " ", n(y), "H", n(x + w), "V", n(y + h), "H", n(x), "Z"});
        } else {
            const std::string corner = "A" + n(rx) + " " + n(ry) + " 0 0 1 ";
            add({"M", n(x + rx), " ", n(y), "H", n(x + w - rx),
                 corner, n(x + w), " ", n(y + ry), "V", n(y + h - ry),
                 corner, n(x + w - rx), " ", n(y + h), "H", n(x + rx),
                 corner, n(x), " ", n(y + h - ry), "V", n(y + ry),
                 corner, n(x + rx), " ", n(y), "Z"});
        }
    } else if (iequals(name, "circle") || iequals(name, "ellipse")) {
        const double cx = length(tag, "cx"), cy = length(tag, "cy");
        const bool circle = iequals(name, "circle");
        const double rx = circle ? length(tag, "r") : length(tag, "rx");
        const double ry = circle ? rx : length(tag, "ry");
        if (rx <= 0 || ry <= 0) return {};
        // Two half arcs; a single arc cannot start and end at the same point.
        const std::string arc = "A" + n(rx) + " " + n(ry) + " 0 1 1 ";
        add({"M", n(cx + rx), " ", n(cy), arc, n(cx - rx), " ", n(cy),
             arc, n(cx + rx), " ", n(cy), "Z"});
    }
    return d;
}

// Moves raw[first...] from an element's own coordinates to document
// coordinates. Arcs stay arcs under rotation, uniform scaling and reflection;
// otherwise they are no longer circular and are flattened into stitches.
void transform_raws(const Affine& m, std::vector<Raw>& raw, std::size_t first) {
    if (m.identity()) return;
    const bool keep_arcs = m.similarity();
    const bool mirrored = m.det() < 0;
    std::vector<Raw> moved;
    moved.reserve(raw.size() - first);
    for (auto i = first; i < raw.size(); ++i) {
        Raw r = raw[i];
        if (r.kind == Raw::Kind::Arc && !keep_arcs) {
            // sample_arc's G03 sweeps towards increasing angle, like `positive`.
            const Move arc{0, r.positive ? 3 : 2, r.end, r.center};
            const auto points = sample_arc(r.start, arc, 2.0);
            for (std::size_t p = 1; p < points.size(); ++p)
                moved.push_back({Raw::Kind::Stitch, m.map(points[p - 1]), m.map(points[p])});
            continue;
        }
        r.start = m.map(r.start);
        r.end = m.map(r.end);
        if (r.kind == Raw::Kind::Arc) {
            r.center = m.map(r.center);
            if (mirrored) r.positive = !r.positive;
        }
        moved.push_back(r);
    }
    raw.resize(first);
    raw.insert(raw.end(), moved.begin(), moved.end());
}

// A closed axis-aligned rectangle made of one subpath: the shape of a
// registration frame.
bool is_rectangle(const std::vector<Raw>& raw, std::size_t first) {
    if (raw.size() != first + 5 || raw[first].kind != Raw::Kind::Travel) return false;
    for (auto n = first + 1; n < raw.size(); ++n) {
        const auto& r = raw[n];
        if (r.kind != Raw::Kind::Stitch ||
            (std::abs(r.end.x - r.start.x) >= .01 && std::abs(r.end.y - r.start.y) >= .01))
            return false;
    }
    return distance(raw[first].end, raw.back().end) < .01;
}

// Elements whose contents are never drawn directly.
bool hidden_container(std::string_view name) {
    static const std::array<std::string_view, 12> names{
        "defs", "clipPath", "mask", "symbol", "marker", "pattern", "linearGradient",
        "radialGradient", "style", "script", "text", "metadata"};
    return std::any_of(names.begin(), names.end(),
                       [name](std::string_view n) { return iequals(n, name); });
}

} // namespace

Pattern read_svg(const std::filesystem::path& path, double bezier_tolerance) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("Cannot open SVG file: " + path_text(path));
    const std::string xml((std::istreambuf_iterator<char>(in)), {});

    std::size_t cursor = 0;
    std::string_view root;
    for (Tag tag = next_element(xml, cursor); !tag.text.empty(); tag = next_element(xml, cursor))
        if (!tag.closing && iequals(tag.name, "svg")) { root = tag.text; break; }
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

    // Walk the elements, tracking what groups pass down to their children.
    struct Context {
        std::string_view name;
        Affine transform;
        std::string stroke;  // empty when no ancestor sets it
        std::string fill;
        bool hidden{};
    };
    std::vector<Context> stack{Context{}};
    std::vector<Raw> raw;
    raw.reserve(4096);
    std::vector<std::pair<std::size_t, std::size_t>> rectangles;  // raw ranges
    cursor = 0;
    for (Tag tag = next_element(xml, cursor); !tag.text.empty(); tag = next_element(xml, cursor)) {
        if (tag.closing) {
            // Pop to the matching element, tolerating unbalanced markup.
            for (auto i = stack.size(); i-- > 1;)
                if (iequals(stack[i].name, tag.name)) { stack.resize(i); break; }
            continue;
        }
        const Context& parent = stack.back();
        Context context{tag.name, parent.transform, parent.stroke, parent.fill, parent.hidden};
        if (const auto own = attribute(tag.text, "transform"); !own.empty())
            context.transform = parent.transform * parse_transform(own);
        if (auto stroke = lower(property(tag.text, "stroke")); !stroke.empty() && stroke != "inherit")
            context.stroke = std::move(stroke);
        if (auto fill = lower(property(tag.text, "fill")); !fill.empty() && fill != "inherit")
            context.fill = std::move(fill);
        if (hidden_container(tag.name) || lower(property(tag.text, "display")) == "none")
            context.hidden = true;
        const bool visible = !context.hidden &&
            lower(property(tag.text, "visibility")) != "hidden" &&
            !decorative(context.stroke, context.fill);
        if (visible) {
            const std::string d = shape_path(tag.name, tag.text);
            if (!d.empty()) {
                const auto first = raw.size();
                const double scale = svg_scale.value_or(1.0) * std::max(context.transform.scale(), 1e-12);
                parse_path(d, bezier_tolerance / scale, raw);
                transform_raws(context.transform, raw, first);
                if (is_rectangle(raw, first)) rectangles.emplace_back(first, raw.size());
            }
        }
        if (!tag.self_closing) stack.push_back(std::move(context));
    }

    // A rectangle around everything else is a registration frame, not part of
    // the design. Rectangles inside the design, or a design that is only
    // rectangles, are kept.
    if (!rectangles.empty()) {
        std::vector<char> frame_part(raw.size(), 0);
        for (const auto& [first, last] : rectangles)
            std::fill(frame_part.begin() + first, frame_part.begin() + last, 1);
        Bounds others;
        auto include = [](Bounds& b, Point p) {
            if (!b.valid) { b = {p.x, p.y, p.x, p.y, true}; return; }
            b.min_x = std::min(b.min_x, p.x); b.max_x = std::max(b.max_x, p.x);
            b.min_y = std::min(b.min_y, p.y); b.max_y = std::max(b.max_y, p.y);
        };
        for (std::size_t i = 0; i < raw.size(); ++i) {
            if (frame_part[i]) continue;
            if (raw[i].kind != Raw::Kind::Travel) include(others, raw[i].start);
            include(others, raw[i].end);
        }
        std::vector<char> drop(raw.size(), 0);
        for (const auto& [first, last] : rectangles) {
            Bounds frame;
            for (auto i = first; i < last; ++i) include(frame, raw[i].end);
            const double tolerance = .01;
            if (others.valid && frame.min_x <= others.min_x + tolerance &&
                frame.min_y <= others.min_y + tolerance &&
                frame.max_x >= others.max_x - tolerance &&
                frame.max_y >= others.max_y - tolerance)
                std::fill(drop.begin() + first, drop.begin() + last, 1);
        }
        std::size_t kept = 0;
        for (std::size_t i = 0; i < raw.size(); ++i)
            if (!drop[i]) raw[kept++] = raw[i];
        raw.resize(kept);
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
    // SVG's y axis points down and PAT's points up.
    auto transform = [&](Point p) {
        return Point{rounded4((p.x - min_x) * *svg_scale),
                     rounded4((max_y - p.y) * *svg_scale)};
    };

    result.moves.reserve(raw.size() + 1);
    int sequence = 10;
    result.moves.push_back({sequence++, 0, {0.0, 0.0}, std::nullopt});
    for (const auto& r : raw) {
        if (r.kind == Raw::Kind::Travel) {
            result.moves.push_back({sequence++, 0, transform(r.end), std::nullopt});
        } else if (distance(transform(r.start), transform(r.end)) > 1e-6) {
            if (r.kind == Raw::Kind::Arc) {
                // Flipping y turns an increasing-angle sweep clockwise: G02.
                const int gcode = r.positive ? 2 : 3;
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
