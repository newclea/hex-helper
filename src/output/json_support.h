#pragma once

#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>

#include "lol_assistant/common/timestamp.h"

namespace lol_assistant::output::detail {

inline void AppendReplacementCharacter(std::string& output) {
  output.append("\xEF\xBF\xBD", 3U);
}

[[nodiscard]] inline std::string SanitizeUtf8(const std::string_view input) {
  std::string output;
  output.reserve(input.size());
  std::size_t index = 0U;
  while (index < input.size()) {
    const auto first = static_cast<unsigned char>(input[index]);
    if (first <= 0x7FU) {
      output.push_back(static_cast<char>(first));
      ++index;
      continue;
    }

    std::size_t length = 0U;
    std::uint32_t code_point = 0U;
    std::uint32_t minimum = 0U;
    if (first >= 0xC2U && first <= 0xDFU) {
      length = 2U;
      code_point = first & 0x1FU;
      minimum = 0x80U;
    } else if (first >= 0xE0U && first <= 0xEFU) {
      length = 3U;
      code_point = first & 0x0FU;
      minimum = 0x800U;
    } else if (first >= 0xF0U && first <= 0xF4U) {
      length = 4U;
      code_point = first & 0x07U;
      minimum = 0x10000U;
    } else {
      AppendReplacementCharacter(output);
      ++index;
      continue;
    }

    if (input.size() - index < length) {
      AppendReplacementCharacter(output);
      ++index;
      continue;
    }
    bool continuation_valid = true;
    for (std::size_t offset = 1U; offset < length; ++offset) {
      const auto continuation =
          static_cast<unsigned char>(input[index + offset]);
      if ((continuation & 0xC0U) != 0x80U) {
        continuation_valid = false;
        break;
      }
      code_point = (code_point << 6U) | (continuation & 0x3FU);
    }
    if (!continuation_valid || code_point < minimum ||
        code_point > 0x10FFFFU ||
        (code_point >= 0xD800U && code_point <= 0xDFFFU)) {
      AppendReplacementCharacter(output);
      ++index;
      continue;
    }
    output.append(input.substr(index, length));
    index += length;
  }
  return output;
}

inline void AppendQuotedUtf8(std::string& output,
                             const std::string_view input) {
  const std::string sanitized = SanitizeUtf8(input);
  constexpr char hex[] = "0123456789abcdef";
  output.push_back('"');
  for (const unsigned char value : sanitized) {
    switch (value) {
      case '"':
        output.append("\\\"");
        break;
      case '\\':
        output.append("\\\\");
        break;
      case '\b':
        output.append("\\b");
        break;
      case '\f':
        output.append("\\f");
        break;
      case '\n':
        output.append("\\n");
        break;
      case '\r':
        output.append("\\r");
        break;
      case '\t':
        output.append("\\t");
        break;
      default:
        if (value < 0x20U) {
          output.append("\\u00");
          output.push_back(hex[(value >> 4U) & 0x0FU]);
          output.push_back(hex[value & 0x0FU]);
        } else {
          output.push_back(static_cast<char>(value));
        }
        break;
    }
  }
  output.push_back('"');
}

template <typename Floating>
inline void AppendFloating(std::string& output, const Floating value) {
  if (!std::isfinite(value)) {
    throw std::invalid_argument("JSON cannot represent a non-finite number");
  }
  std::array<char, 64U> buffer{};
  const auto result = std::to_chars(
      buffer.data(), buffer.data() + buffer.size(), value,
      std::chars_format::general, std::numeric_limits<Floating>::max_digits10);
  if (result.ec != std::errc{}) {
    throw std::runtime_error("Failed to format JSON number");
  }
  output.append(buffer.data(), result.ptr);
}

[[nodiscard]] inline std::string FormatUtcTimestamp(
    const common::UtcTimestamp timestamp) {
  using namespace std::chrono;
  const auto since_epoch = timestamp.time_since_epoch();
  const auto whole_seconds = floor<seconds>(since_epoch);
  const auto fractional = duration_cast<microseconds>(since_epoch - whole_seconds);
  const std::time_t time_value =
      static_cast<std::time_t>(whole_seconds.count());
  std::tm utc{};
  if (gmtime_s(&utc, &time_value) != 0) {
    throw std::runtime_error("Unable to format UTC timestamp");
  }
  std::array<char, 40U> buffer{};
  const int written = std::snprintf(
      buffer.data(), buffer.size(),
      "%04d-%02d-%02dT%02d:%02d:%02d.%06lldZ", utc.tm_year + 1900,
      utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec,
      static_cast<long long>(fractional.count()));
  if (written <= 0 || static_cast<std::size_t>(written) >= buffer.size()) {
    throw std::runtime_error("Unable to format UTC timestamp");
  }
  return std::string(buffer.data(), static_cast<std::size_t>(written));
}

}  // namespace lol_assistant::output::detail
