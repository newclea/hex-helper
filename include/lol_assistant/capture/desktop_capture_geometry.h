#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace lol_assistant::capture {

enum class DesktopCaptureTargetKind : std::uint8_t {
  Monitor,
  WindowClient,
  WindowExtendedFrame,
};

enum class PartialVisibilityPolicy : std::uint8_t {
  PauseUnlessFullyVisible,
  CropVisibleIntersection,
};

enum class DesktopGeometryStatus : std::uint8_t {
  Ready,
  InvalidTarget,
  Minimized,
  Hidden,
  Offscreen,
  PartiallyOffscreen,
  CrossOutput,
  NoOutputs,
};

struct DesktopCaptureTarget final {
  DesktopCaptureTargetKind kind{DesktopCaptureTargetKind::Monitor};
  HMONITOR monitor{nullptr};
  HWND window{nullptr};
};

struct DesktopOutputGeometry final {
  HMONITOR monitor{nullptr};
  RECT physical_bounds{};
};

struct DesktopCaptureGeometry final {
  DesktopGeometryStatus status{DesktopGeometryStatus::InvalidTarget};
  HMONITOR monitor{nullptr};
  RECT output_physical{};
  RECT requested_physical{};
  RECT visible_physical{};
  RECT source_rect{};
  POINT visible_origin_in_target{};
  bool minimized{false};
  bool hidden{false};
  bool fully_visible{false};
  bool dpi_context_applied{false};

  [[nodiscard]] bool IsReady() const noexcept {
    return status == DesktopGeometryStatus::Ready;
  }
};

[[nodiscard]] RECT MapLogicalRectToPhysical(
    const RECT& logical_rect, const RECT& logical_monitor,
    const RECT& physical_monitor) noexcept;

[[nodiscard]] DesktopCaptureGeometry PlanDesktopCaptureGeometry(
    const RECT& requested_physical, bool minimized, bool hidden,
    std::span<const DesktopOutputGeometry> outputs,
    PartialVisibilityPolicy policy) noexcept;

[[nodiscard]] std::vector<DesktopOutputGeometry>
EnumerateDesktopOutputGeometry();

[[nodiscard]] DesktopCaptureGeometry ResolveDesktopCaptureGeometry(
    const DesktopCaptureTarget& target, PartialVisibilityPolicy policy,
    std::string* error = nullptr) noexcept;

}  // namespace lol_assistant::capture
