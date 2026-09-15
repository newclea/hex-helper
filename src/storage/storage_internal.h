#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>
#include <system_error>

#include "lol_assistant/storage/storage_types.h"

namespace lol_assistant::storage::internal {

struct WorkspacePath final {
  std::filesystem::path root{};
  std::filesystem::path target{};
  std::filesystem::path relative{};
};

[[nodiscard]] inline std::string PathToUtf8(
    const std::filesystem::path& path) {
  const auto bytes = path.generic_u8string();
  return std::string{bytes.begin(), bytes.end()};
}

[[nodiscard]] inline bool IsValidUtf8(const std::string_view value) noexcept {
  std::size_t index = 0U;
  while (index < value.size()) {
    const auto first = static_cast<std::uint8_t>(value[index]);
    if (first <= 0x7FU) {
      ++index;
      continue;
    }

    std::size_t continuation_count = 0U;
    std::uint32_t code_point = 0U;
    if (first >= 0xC2U && first <= 0xDFU) {
      continuation_count = 1U;
      code_point = first & 0x1FU;
    } else if (first >= 0xE0U && first <= 0xEFU) {
      continuation_count = 2U;
      code_point = first & 0x0FU;
    } else if (first >= 0xF0U && first <= 0xF4U) {
      continuation_count = 3U;
      code_point = first & 0x07U;
    } else {
      return false;
    }
    if (index + continuation_count >= value.size()) {
      return false;
    }
    for (std::size_t offset = 1U; offset <= continuation_count; ++offset) {
      const auto continuation =
          static_cast<std::uint8_t>(value[index + offset]);
      if ((continuation & 0xC0U) != 0x80U) {
        return false;
      }
      code_point = (code_point << 6U) | (continuation & 0x3FU);
    }
    if ((continuation_count == 2U &&
         (code_point < 0x800U ||
          (code_point >= 0xD800U && code_point <= 0xDFFFU))) ||
        (continuation_count == 3U &&
         (code_point < 0x10000U || code_point > 0x10FFFFU))) {
      return false;
    }
    index += continuation_count + 1U;
  }
  return true;
}

[[nodiscard]] inline StorageStatus ResolveWorkspacePath(
    const std::filesystem::path& requested_path,
    const std::filesystem::path& workspace_root,
    WorkspacePath& resolved) {
  if (requested_path.empty() || workspace_root.empty() ||
      !requested_path.is_absolute() || !workspace_root.is_absolute()) {
    return {StorageErrorCode::InvalidPath, 0,
            "workspace root and target path must both be explicit absolute paths"};
  }

  std::error_code error;
  const auto canonical_root =
      std::filesystem::weakly_canonical(workspace_root, error);
  if (error || !std::filesystem::is_directory(canonical_root, error) || error) {
    return {StorageErrorCode::InvalidPath, error.value(),
            "workspace root does not resolve to an existing directory"};
  }

  std::filesystem::path canonical_target;
  if (std::filesystem::exists(requested_path, error) && !error) {
    canonical_target = std::filesystem::weakly_canonical(requested_path, error);
  } else {
    error.clear();
    const auto canonical_parent =
        std::filesystem::weakly_canonical(requested_path.parent_path(), error);
    if (!error && std::filesystem::is_directory(canonical_parent, error) &&
        !error) {
      canonical_target = canonical_parent / requested_path.filename();
    }
  }
  if (error || canonical_target.empty()) {
    return {StorageErrorCode::InvalidPath, error.value(),
            "target parent does not resolve to an existing directory"};
  }

  const auto relative =
      std::filesystem::relative(canonical_target, canonical_root, error);
  if (error || relative.empty() || relative == "." || relative.is_absolute()) {
    return {StorageErrorCode::InvalidPath, error.value(),
            "target path must be a child of the workspace root"};
  }
  const auto first = *relative.begin();
  if (first == "..") {
    return {StorageErrorCode::InvalidPath, 0,
            "target path escapes the workspace root"};
  }

  resolved = WorkspacePath{canonical_root, canonical_target, relative};
  return StorageStatus::Ok();
}

}  // namespace lol_assistant::storage::internal
