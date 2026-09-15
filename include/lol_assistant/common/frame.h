#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/common/timestamp.h"

namespace lol_assistant::common {

enum class FrameSourceKind : std::uint8_t {
  Unknown = 0,
  WindowsGraphicsCapture = 1,
  DesktopDuplication = 2,
  Replay = 3,
  Stub = 4,
};

struct FrameSource final {
  FrameSourceKind kind{FrameSourceKind::Unknown};
  std::string id{};

  [[nodiscard]] bool IsValid() const noexcept;
};

// A Frame always owns a CPU-resident BGRA8 buffer. stride is measured in
// bytes, can include row padding, and buffer.size() must equal stride*height.
struct Frame final {
  static constexpr std::size_t kBytesPerPixel = 4U;

  FrameSource source{};
  std::uint64_t frame_id{0U};
  FrameTimestamps timestamps{};
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  std::uint32_t stride{0U};
  std::vector<std::uint8_t> buffer{};

  [[nodiscard]] std::optional<std::size_t> RequiredBufferSize() const noexcept;
  [[nodiscard]] bool IsValid() const noexcept;
};

}  // namespace lol_assistant::common
