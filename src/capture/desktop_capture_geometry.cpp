#include "lol_assistant/capture/desktop_capture_geometry.h"

#include <dwmapi.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>

namespace lol_assistant::capture {
namespace {

class ScopedPerMonitorV2 final {
 public:
  ScopedPerMonitorV2() noexcept
      : previous_(SetThreadDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)),
        applied_(previous_ != nullptr) {}

  ~ScopedPerMonitorV2() {
    if (applied_) {
      static_cast<void>(SetThreadDpiAwarenessContext(previous_));
    }
  }

  ScopedPerMonitorV2(const ScopedPerMonitorV2&) = delete;
  ScopedPerMonitorV2& operator=(const ScopedPerMonitorV2&) = delete;

  [[nodiscard]] bool Applied() const noexcept { return applied_; }

 private:
  DPI_AWARENESS_CONTEXT previous_{nullptr};
  bool applied_{false};
};

[[nodiscard]] LONG Width(const RECT& rectangle) noexcept {
  return rectangle.right - rectangle.left;
}

[[nodiscard]] LONG Height(const RECT& rectangle) noexcept {
  return rectangle.bottom - rectangle.top;
}

[[nodiscard]] bool HasArea(const RECT& rectangle) noexcept {
  return Width(rectangle) > 0 && Height(rectangle) > 0;
}

[[nodiscard]] bool EqualRectValue(const RECT& left,
                                  const RECT& right) noexcept {
  return left.left == right.left && left.top == right.top &&
         left.right == right.right && left.bottom == right.bottom;
}

[[nodiscard]] RECT Intersection(const RECT& left,
                                const RECT& right) noexcept {
  RECT result{std::max(left.left, right.left),
              std::max(left.top, right.top),
              std::min(left.right, right.right),
              std::min(left.bottom, right.bottom)};
  if (!HasArea(result)) {
    return {};
  }
  return result;
}

BOOL CALLBACK CollectMonitor(const HMONITOR monitor, HDC, LPRECT,
                             const LPARAM parameter) {
  auto* const outputs =
      reinterpret_cast<std::vector<DesktopOutputGeometry>*>(parameter);
  MONITORINFO info{};
  info.cbSize = sizeof(info);
  if (outputs != nullptr && GetMonitorInfoW(monitor, &info) != FALSE) {
    outputs->push_back({monitor, info.rcMonitor});
  }
  return TRUE;
}

[[nodiscard]] bool ResolveWindowRect(const DesktopCaptureTarget& target,
                                     RECT* const rectangle) noexcept {
  if (target.window == nullptr || rectangle == nullptr ||
      IsWindow(target.window) == FALSE) {
    return false;
  }
  if (target.kind == DesktopCaptureTargetKind::WindowClient) {
    RECT client{};
    if (GetClientRect(target.window, &client) == FALSE) {
      return false;
    }
    POINT corners[]{POINT{client.left, client.top},
                    POINT{client.right, client.bottom}};
    SetLastError(ERROR_SUCCESS);
    const int mapped = MapWindowPoints(target.window, nullptr, corners, 2U);
    if (mapped == 0 && GetLastError() != ERROR_SUCCESS) {
      return false;
    }
    *rectangle = RECT{corners[0].x, corners[0].y, corners[1].x,
                      corners[1].y};
    return HasArea(*rectangle);
  }

  if (SUCCEEDED(DwmGetWindowAttribute(
          target.window, DWMWA_EXTENDED_FRAME_BOUNDS, rectangle,
          static_cast<DWORD>(sizeof(*rectangle)))) &&
      HasArea(*rectangle)) {
    return true;
  }
  return GetWindowRect(target.window, rectangle) != FALSE &&
         HasArea(*rectangle);
}

[[nodiscard]] LONG MapEndpoint(const LONG value, const LONG logical_origin,
                               const LONG logical_extent,
                               const LONG physical_origin,
                               const LONG physical_extent) noexcept {
  if (logical_extent <= 0 || physical_extent <= 0) {
    return physical_origin;
  }
  const double offset = static_cast<double>(value - logical_origin);
  const double scaled =
      offset * static_cast<double>(physical_extent) /
      static_cast<double>(logical_extent);
  const double mapped = static_cast<double>(physical_origin) + scaled;
  const double minimum = static_cast<double>(std::numeric_limits<LONG>::min());
  const double maximum = static_cast<double>(std::numeric_limits<LONG>::max());
  return static_cast<LONG>(std::clamp(std::llround(mapped),
                                     static_cast<long long>(minimum),
                                     static_cast<long long>(maximum)));
}

}  // namespace

RECT MapLogicalRectToPhysical(const RECT& logical_rect,
                              const RECT& logical_monitor,
                              const RECT& physical_monitor) noexcept {
  return RECT{
      MapEndpoint(logical_rect.left, logical_monitor.left,
                  Width(logical_monitor), physical_monitor.left,
                  Width(physical_monitor)),
      MapEndpoint(logical_rect.top, logical_monitor.top,
                  Height(logical_monitor), physical_monitor.top,
                  Height(physical_monitor)),
      MapEndpoint(logical_rect.right, logical_monitor.left,
                  Width(logical_monitor), physical_monitor.left,
                  Width(physical_monitor)),
      MapEndpoint(logical_rect.bottom, logical_monitor.top,
                  Height(logical_monitor), physical_monitor.top,
                  Height(physical_monitor))};
}

DesktopCaptureGeometry PlanDesktopCaptureGeometry(
    const RECT& requested_physical, const bool minimized, const bool hidden,
    const std::span<const DesktopOutputGeometry> outputs,
    const PartialVisibilityPolicy policy) noexcept {
  DesktopCaptureGeometry result{};
  result.requested_physical = requested_physical;
  result.minimized = minimized;
  result.hidden = hidden;
  if (minimized) {
    result.status = DesktopGeometryStatus::Minimized;
    return result;
  }
  if (hidden) {
    result.status = DesktopGeometryStatus::Hidden;
    return result;
  }
  if (!HasArea(requested_physical)) {
    result.status = DesktopGeometryStatus::InvalidTarget;
    return result;
  }
  if (outputs.empty()) {
    result.status = DesktopGeometryStatus::NoOutputs;
    return result;
  }

  std::size_t intersections = 0U;
  RECT visible{};
  DesktopOutputGeometry selected{};
  for (const auto& output : outputs) {
    const RECT candidate = Intersection(requested_physical,
                                        output.physical_bounds);
    if (!HasArea(candidate)) {
      continue;
    }
    ++intersections;
    if (intersections == 1U) {
      selected = output;
      visible = candidate;
    }
  }
  if (intersections == 0U) {
    result.status = DesktopGeometryStatus::Offscreen;
    return result;
  }
  if (intersections > 1U) {
    result.status = DesktopGeometryStatus::CrossOutput;
    return result;
  }

  result.monitor = selected.monitor;
  result.output_physical = selected.physical_bounds;
  result.visible_physical = visible;
  result.fully_visible = EqualRectValue(visible, requested_physical);
  result.visible_origin_in_target =
      POINT{visible.left - requested_physical.left,
            visible.top - requested_physical.top};
  result.source_rect =
      RECT{visible.left - selected.physical_bounds.left,
           visible.top - selected.physical_bounds.top,
           visible.right - selected.physical_bounds.left,
           visible.bottom - selected.physical_bounds.top};
  if (!result.fully_visible &&
      policy == PartialVisibilityPolicy::PauseUnlessFullyVisible) {
    result.status = DesktopGeometryStatus::PartiallyOffscreen;
    return result;
  }
  result.status = DesktopGeometryStatus::Ready;
  return result;
}

std::vector<DesktopOutputGeometry> EnumerateDesktopOutputGeometry() {
  ScopedPerMonitorV2 dpi_scope;
  std::vector<DesktopOutputGeometry> outputs;
  static_cast<void>(EnumDisplayMonitors(
      nullptr, nullptr, &CollectMonitor,
      reinterpret_cast<LPARAM>(&outputs)));
  return outputs;
}

DesktopCaptureGeometry ResolveDesktopCaptureGeometry(
    const DesktopCaptureTarget& target,
    const PartialVisibilityPolicy policy, std::string* const error) noexcept {
  try {
    ScopedPerMonitorV2 dpi_scope;
    const auto outputs = EnumerateDesktopOutputGeometry();
    RECT requested{};
    bool minimized = false;
    bool hidden = false;

    if (target.kind == DesktopCaptureTargetKind::Monitor) {
      const auto found = std::find_if(
          outputs.begin(), outputs.end(), [&target](const auto& output) {
            return output.monitor == target.monitor;
          });
      if (target.monitor == nullptr || found == outputs.end()) {
        if (error != nullptr) {
          *error = "The requested monitor is not attached to the desktop";
        }
        DesktopCaptureGeometry invalid{};
        invalid.status = DesktopGeometryStatus::InvalidTarget;
        invalid.dpi_context_applied = dpi_scope.Applied();
        return invalid;
      }
      requested = found->physical_bounds;
    } else {
      minimized = IsIconic(target.window) != FALSE;
      hidden = IsWindowVisible(target.window) == FALSE;
      if (!ResolveWindowRect(target, &requested)) {
        if (error != nullptr) {
          *error = "The requested window does not have a physical capture rect";
        }
        DesktopCaptureGeometry invalid{};
        invalid.status = DesktopGeometryStatus::InvalidTarget;
        invalid.dpi_context_applied = dpi_scope.Applied();
        return invalid;
      }
    }

    auto result = PlanDesktopCaptureGeometry(requested, minimized, hidden,
                                             outputs, policy);
    result.dpi_context_applied = dpi_scope.Applied();
    if (error != nullptr) {
      error->clear();
    }
    return result;
  } catch (...) {
    if (error != nullptr) {
      *error = "Unable to resolve desktop capture geometry";
    }
    DesktopCaptureGeometry invalid{};
    invalid.status = DesktopGeometryStatus::InvalidTarget;
    return invalid;
  }
}

}  // namespace lol_assistant::capture
