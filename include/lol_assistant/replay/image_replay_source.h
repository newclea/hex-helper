#pragma once

#include <cstddef>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <vector>

#include "lol_assistant/common/frame_source.h"

namespace lol_assistant::replay {

enum class ReplayInputKind {
  SingleFile,
  Directory,
  ManifestJsonLines,
};

struct ReplayOpenOptions final {
  bool loop{false};
};

class ReplayError final : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// An eager, deterministic replay source. Construction validates every input
// and decodes every image, so corrupt frames and manifest mismatches fail
// before playback starts.
class ImageReplaySource final : public common::IFrameSource {
 public:
  ImageReplaySource(std::filesystem::path input, ReplayInputKind kind,
                    ReplayOpenOptions options = {});

  [[nodiscard]] common::FrameSource Source() const override;
  [[nodiscard]] std::optional<common::Frame> TryGetNextFrame() override;

  // Returns one frame even while paused, then advances the cursor.
  [[nodiscard]] std::optional<common::Frame> Next();

  void SetPaused(bool paused) noexcept;
  [[nodiscard]] bool IsPaused() const noexcept;
  void SetLoop(bool loop) noexcept;
  [[nodiscard]] bool IsLooping() const noexcept;

  // Seeks to the zero-based frame index. Invalid indices are rejected without
  // changing the current cursor.
  [[nodiscard]] bool SeekIndex(std::size_t index) noexcept;
  [[nodiscard]] std::size_t CurrentIndex() const noexcept;
  [[nodiscard]] std::size_t FrameCount() const noexcept;

 private:
  std::filesystem::path input_{};
  common::FrameSource source_{};
  std::vector<common::Frame> frames_{};
  std::size_t cursor_{0U};
  bool paused_{false};
  bool loop_{false};
};

}  // namespace lol_assistant::replay
