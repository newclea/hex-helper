#pragma once

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/roi.h"

namespace lol_assistant::collection {

inline constexpr std::string_view kSampleMetadataSchema =
    "lol_assistant.sample_collection";
inline constexpr std::uint32_t kSampleMetadataVersion = 1U;
// Shared with data/dataset/augment_offers/schema.json. The collector-specific
// schema/version fields above remain for compatibility with existing readers.
inline constexpr std::uint32_t kDatasetMetadataSchemaVersion = 1U;

enum class SampleKind : std::uint8_t {
  Real = 0,
  Synthetic = 1,
  Unknown = 2,
};

enum class CaptureReason : std::uint8_t {
  DetectorSuspect = 0,
  ManualF8 = 1,
};

enum class OcrSampleStatus : std::uint8_t {
  NotRun = 0,
  BackendUnavailable = 1,
  Failed = 2,
  Unknown = 3,
  Matched = 4,
};

struct WindowMetadata final {
  std::optional<std::string> title{};
  std::optional<std::string> id{};
  std::optional<std::uint32_t> process_id{};
};

struct DetectorMetadata final {
  bool visible{false};
  float score{0.0F};
  std::string reason{"not_evaluated"};
};

struct CardRoiMetadata final {
  detector::PixelRoi card{};
  std::optional<detector::PixelRoi> title{};
  std::optional<detector::PixelRoi> icon{};
};

struct SampleRois final {
  detector::PixelRoi offer{};
  // Fixed semantic order: Left, Center, Right.
  std::array<CardRoiMetadata, common::kAugmentCardCount> cards{};
};

struct CardOcrMetadata final {
  std::string raw{};
  std::vector<std::string> lines{};
  std::optional<std::string> matched_id{};
  OcrSampleStatus status{OcrSampleStatus::NotRun};
  // Nullable because some OCR backends truthfully expose no confidence.
  std::optional<float> confidence{};
};

struct SampleCollectionRequest final {
  SampleKind sample_kind{SampleKind::Unknown};
  CaptureReason capture_reason{CaptureReason::DetectorSuspect};
  std::optional<std::string> capture_reason_detail{};
  // Falls back to frame.timestamps.captured_at_utc, then system_clock::now().
  std::optional<common::UtcTimestamp> timestamp{};
  // Optional deterministic clock input for tests/replay. It is not serialized.
  std::optional<common::MonotonicTimestamp> dedup_observed_at{};
  WindowMetadata window{};
  std::optional<double> ui_scale{};
  SampleRois rois{};
  DetectorMetadata detector{};
  std::array<CardOcrMetadata, common::kAugmentCardCount> cards{};
};

struct SampleDedupPolicy final {
  // Similar pages are suppressed only inside this interval.
  std::chrono::milliseconds minimum_interval{std::chrono::seconds{5}};
  std::uint32_t maximum_dhash_distance{4U};
  // A manual request skips the interval+dHash gate by default.
  bool manual_bypasses_minimum_interval{true};
  // Exact raw-frame bytes remain protected unless explicitly overridden.
  bool manual_allows_exact_duplicate{false};
};

struct SampleCollectorConfig final {
  // Must be an absolute path without any '..' component.
  // When the final component is real, synthetic, or unknown, Collect also
  // requires the request provenance to match that dataset bucket.
  std::filesystem::path dataset_root{};
  SampleDedupPolicy dedup{};
};

// A narrow seam for alternate encoders and deterministic failure testing.
// The default writer delegates to replay::WicImageCodec::SavePng.
class ISampleImageWriter {
 public:
  virtual ~ISampleImageWriter() = default;
  virtual void SavePng(const common::Frame& frame,
                       const std::filesystem::path& path) const = 0;
};

enum class CollectStatus : std::uint8_t {
  Saved = 0,
  Duplicate = 1,
  InvalidRequest = 2,
  IoError = 3,
  SerializationError = 4,
};

struct CollectResult final {
  CollectStatus status{CollectStatus::InvalidRequest};
  // For Saved this is the committed directory; for Duplicate it is the last
  // committed sample that caused suppression.
  std::filesystem::path sample_directory{};
  std::string message{};

  [[nodiscard]] bool saved() const noexcept {
    return status == CollectStatus::Saved;
  }
  [[nodiscard]] bool duplicate() const noexcept {
    return status == CollectStatus::Duplicate;
  }
};

// Exposed so dataset validators can apply the same strict syntax check that is
// run against metadata.json before a sample directory is committed.
[[nodiscard]] bool IsMetadataJsonParseable(std::string_view text) noexcept;

// Thread-safe, stateful consecutive-page collector. It performs no capture,
// detection, recognition, input simulation, or network access.
class SampleCollector final {
 public:
  explicit SampleCollector(
      SampleCollectorConfig config,
      std::shared_ptr<const ISampleImageWriter> image_writer = {});
  ~SampleCollector();

  SampleCollector(const SampleCollector&) = delete;
  SampleCollector& operator=(const SampleCollector&) = delete;
  SampleCollector(SampleCollector&&) = delete;
  SampleCollector& operator=(SampleCollector&&) = delete;

  [[nodiscard]] CollectResult Collect(
      const common::Frame& raw_frame,
      const SampleCollectionRequest& request) noexcept;
  [[nodiscard]] const std::filesystem::path& dataset_root() const noexcept;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace lol_assistant::collection
