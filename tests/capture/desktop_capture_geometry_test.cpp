#include "lol_assistant/capture/desktop_capture_geometry.h"

#include <cstdint>
#include <iostream>
#include <span>

namespace {

int failures = 0;

void Check(const bool condition, const char* const expression) {
  if (!condition) {
    ++failures;
    std::cerr << "[FAIL] " << expression << '\n';
  }
}

#define CHECK(expression) Check((expression), #expression)

HMONITOR Monitor(const std::uintptr_t value) {
  return reinterpret_cast<HMONITOR>(value);
}

void TestEndpointPreservingDpiMapping() {
  using lol_assistant::capture::MapLogicalRectToPhysical;
  const RECT logical_monitor{0, 0, 1707, 1067};
  const RECT physical_monitor{0, 0, 2560, 1600};
  const RECT mapped = MapLogicalRectToPhysical(
      logical_monitor, logical_monitor, physical_monitor);
  CHECK(mapped.left == 0);
  CHECK(mapped.top == 0);
  CHECK(mapped.right == 2560);
  CHECK(mapped.bottom == 1600);

  const RECT negative_logical{-1280, 0, 0, 1024};
  const RECT negative_physical{-1920, 0, 0, 1536};
  const RECT center{-960, 256, -320, 768};
  const RECT mapped_center = MapLogicalRectToPhysical(
      center, negative_logical, negative_physical);
  CHECK(mapped_center.left == -1440);
  CHECK(mapped_center.top == 384);
  CHECK(mapped_center.right == -480);
  CHECK(mapped_center.bottom == 1152);
}

void TestGeometryPlanning() {
  using namespace lol_assistant::capture;
  const DesktopOutputGeometry outputs[]{
      {Monitor(1U), RECT{-1920, 0, 0, 1080}},
      {Monitor(2U), RECT{0, 0, 2560, 1600}},
  };

  auto geometry = PlanDesktopCaptureGeometry(
      RECT{100, 200, 900, 800}, false, false, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::Ready);
  CHECK(geometry.monitor == Monitor(2U));
  CHECK(geometry.visible_origin_in_target.x == 0);
  CHECK(geometry.visible_origin_in_target.y == 0);
  CHECK(geometry.source_rect.left == 100);
  CHECK(geometry.source_rect.top == 200);
  CHECK(geometry.source_rect.right == 900);
  CHECK(geometry.source_rect.bottom == 800);

  geometry = PlanDesktopCaptureGeometry(
      RECT{-100, 100, 100, 500}, false, false, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::CrossOutput);

  geometry = PlanDesktopCaptureGeometry(
      RECT{-2100, 100, -1800, 500}, false, false, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::PartiallyOffscreen);

  geometry = PlanDesktopCaptureGeometry(
      RECT{3000, 100, 3200, 500}, false, false, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::Offscreen);

  geometry = PlanDesktopCaptureGeometry(
      RECT{-2100, 100, -1800, 500}, false, false, outputs,
      PartialVisibilityPolicy::CropVisibleIntersection);
  CHECK(geometry.status == DesktopGeometryStatus::Ready);
  CHECK(!geometry.fully_visible);
  CHECK(geometry.visible_physical.left == -1920);
  CHECK(geometry.visible_origin_in_target.x == 180);

  geometry = PlanDesktopCaptureGeometry(
      RECT{0, 0, 100, 100}, true, false, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::Minimized);
  geometry = PlanDesktopCaptureGeometry(
      RECT{0, 0, 100, 100}, false, true, outputs,
      PartialVisibilityPolicy::PauseUnlessFullyVisible);
  CHECK(geometry.status == DesktopGeometryStatus::Hidden);
}

}  // namespace

int main() {
  TestEndpointPreservingDpiMapping();
  TestGeometryPlanning();
  std::cout << "desktop_capture_geometry_test: failures=" << failures << '\n';
  return failures == 0 ? 0 : 1;
}
