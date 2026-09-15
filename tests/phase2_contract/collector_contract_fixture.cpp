#include "lol_assistant/collection/sample_collector.h"

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

namespace collection = lol_assistant::collection;
namespace common = lol_assistant::common;

[[nodiscard]] collection::SampleKind ParseProvenance(
    const std::string& value) {
  if (value == "real") {
    return collection::SampleKind::Real;
  }
  if (value == "synthetic") {
    return collection::SampleKind::Synthetic;
  }
  if (value == "unknown") {
    return collection::SampleKind::Unknown;
  }
  throw std::invalid_argument("provenance must be real, synthetic, or unknown");
}

[[nodiscard]] common::Frame MakeSyntheticFixtureFrame(
    const collection::SampleKind provenance) {
  const std::uint8_t offset =
      provenance == collection::SampleKind::Real
          ? 17U
          : (provenance == collection::SampleKind::Synthetic ? 83U : 149U);
  common::Frame frame;
  frame.source =
      provenance == collection::SampleKind::Real
          ? common::FrameSource{common::FrameSourceKind::WindowsGraphicsCapture,
                                "contract-fixture-wgc"}
          : common::FrameSource{common::FrameSourceKind::Replay,
                                provenance == collection::SampleKind::Synthetic
                                    ? "contract-fixture-replay-synthetic"
                                    : "contract-fixture-replay-unknown"};
  frame.frame_id = static_cast<std::uint64_t>(offset);
  frame.width = 96U;
  frame.height = 60U;
  frame.stride = frame.width * 4U;
  frame.timestamps.captured_at_utc = common::UtcTimestamp{
      std::chrono::seconds{1'700'000'000 + offset}};
  frame.buffer.resize(static_cast<std::size_t>(frame.stride) * frame.height);
  for (std::uint32_t y = 0U; y < frame.height; ++y) {
    for (std::uint32_t x = 0U; x < frame.width; ++x) {
      const auto pixel = static_cast<std::size_t>(y) * frame.stride +
                         static_cast<std::size_t>(x) * 4U;
      frame.buffer[pixel + 0U] =
          static_cast<std::uint8_t>((x + offset) % 256U);
      frame.buffer[pixel + 1U] =
          static_cast<std::uint8_t>((y * 3U + offset) % 256U);
      frame.buffer[pixel + 2U] =
          static_cast<std::uint8_t>((x + y + offset * 2U) % 256U);
      frame.buffer[pixel + 3U] = 255U;
    }
  }
  return frame;
}

[[nodiscard]] collection::SampleCollectionRequest MakeRequest(
    const collection::SampleKind provenance) {
  collection::SampleCollectionRequest request;
  request.sample_kind = provenance;
  request.capture_reason = collection::CaptureReason::ManualF8;
  request.capture_reason_detail = "phase2_contract_synthetic_fixture";
  request.ui_scale = 1.0;
  if (provenance == collection::SampleKind::Real) {
    request.window.id = "contract-fixture-window";
  }
  request.rois.offer = {0U, 0U, 96U, 60U};
  request.rois.cards = {
      collection::CardRoiMetadata{{0U, 0U, 30U, 60U}, std::nullopt,
                                  std::nullopt},
      collection::CardRoiMetadata{{33U, 0U, 30U, 60U}, std::nullopt,
                                  std::nullopt},
      collection::CardRoiMetadata{{66U, 0U, 30U, 60U}, std::nullopt,
                                  std::nullopt}};
  request.detector = {false, 0.25F, "contract_fixture_not_a_live_detection"};
  return request;
}

}  // namespace

int main(const int argc, const char* const argv[]) {
  try {
    if (argc != 3) {
      std::cerr << "usage: collector_contract_fixture <bucket-root> "
                   "<real|synthetic|unknown>\n";
      return 2;
    }
    const std::filesystem::path bucket_root =
        std::filesystem::absolute(std::filesystem::path{argv[1]});
    const auto provenance = ParseProvenance(argv[2]);
    collection::SampleCollector collector({bucket_root, {}});
    const auto result =
        collector.Collect(MakeSyntheticFixtureFrame(provenance),
                          MakeRequest(provenance));
    if (!result.saved()) {
      std::cerr << "collector failed: " << result.message << '\n';
      return 1;
    }
    std::cout << result.sample_directory.filename().string() << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "fixture failed: " << error.what() << '\n';
    return 1;
  }
}
