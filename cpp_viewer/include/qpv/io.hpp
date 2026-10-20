#pragma once

#include "qpv/pattern.hpp"

#include <filesystem>
#include <string_view>

namespace qpv {

[[nodiscard]] Pattern read_pat(const std::filesystem::path& path);
[[nodiscard]] Pattern read_qli(const std::filesystem::path& path);
[[nodiscard]] Pattern read_svg(const std::filesystem::path& path,
                               double bezier_tolerance = 0.5);
[[nodiscard]] Pattern load_pattern(const std::filesystem::path& path,
                                   double bezier_tolerance = 0.5);

void write_pat(const Pattern& pattern, const std::filesystem::path& path);
void write_qli(const Pattern& pattern, const std::filesystem::path& path);
void convert_file(const std::filesystem::path& input,
                  const std::filesystem::path& output,
                  double bezier_tolerance = 0.5);

[[nodiscard]] bool is_supported_file(const std::filesystem::path& path);
[[nodiscard]] std::string lower_extension(const std::filesystem::path& path);

} // namespace qpv
