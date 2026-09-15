#pragma once

#include <cstdint>
#include <filesystem>

#include "lol_assistant/common/frame.h"

namespace lol_assistant::replay {

// Stateless WIC helpers shared by replay loading and debug-output tests.
// Decode always returns a tightly packed, owning BGRA8 frame.
class WicImageCodec final {
 public:
  [[nodiscard]] static common::Frame Decode(
      const std::filesystem::path& path, common::FrameSource source,
      std::uint64_t frame_id = 0U);

  // Writes a new PNG and refuses to overwrite an existing file.
  static void SavePng(const common::Frame& frame,
                      const std::filesystem::path& path);
};

}  // namespace lol_assistant::replay
