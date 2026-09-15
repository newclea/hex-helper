#pragma once

#include <array>
#include <memory>
#include <optional>
#include <string>

#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/frame.h"

namespace lol_assistant::output {

struct DebugPreviewOverlay final {
  std::optional<common::NormalizedRoi> detector_roi{};
  std::array<std::optional<common::CardSlot>, common::kAugmentCardCount>
      card_rois{};
  double fps{0.0};
  std::string status{};
};

// A standalone, non-topmost Win32 preview. It paints caller-provided pixels
// only and contains no capture, injection, hook, or input-control code.
class DebugPreviewWindow final {
 public:
  explicit DebugPreviewWindow(std::string title = "LoL Assistant Debug Preview",
                              int client_width = 1280,
                              int client_height = 720);
  ~DebugPreviewWindow();

  DebugPreviewWindow(const DebugPreviewWindow&) = delete;
  DebugPreviewWindow& operator=(const DebugPreviewWindow&) = delete;

  [[nodiscard]] bool Create();
  [[nodiscard]] bool IsOpen() const noexcept;
  void UpdateFrame(const common::Frame& frame,
                   const DebugPreviewOverlay& overlay = {});
  [[nodiscard]] bool PumpMessages();
  void Close() noexcept;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace lol_assistant::output
