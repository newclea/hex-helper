#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>

#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/frame.h"

namespace lol_assistant::output {

enum class DebugArtifactTrigger {
  LowConfidence,
  Conflict,
  Manual,
};

struct DebugArtifactPolicyContext final {
  DebugArtifactTrigger trigger{DebugArtifactTrigger::Manual};
  common::Confidence confidence{};
};

class IDebugArtifactPolicy {
 public:
  virtual ~IDebugArtifactPolicy() = default;
  [[nodiscard]] virtual bool ShouldPersist(
      const DebugArtifactPolicyContext& context) const = 0;
};

class DefaultDebugArtifactPolicy final : public IDebugArtifactPolicy {
 public:
  explicit DefaultDebugArtifactPolicy(float low_confidence_threshold = 0.5F,
                                      bool persist_conflicts = true,
                                      bool persist_manual = true);

  [[nodiscard]] bool ShouldPersist(
      const DebugArtifactPolicyContext& context) const override;

 private:
  float low_confidence_threshold_{0.5F};
  bool persist_conflicts_{true};
  bool persist_manual_{true};
};

struct DebugArtifactRequest final {
  std::string session_id{};
  common::UtcTimestamp timestamp{};
  common::Confidence confidence{};
  std::string backend{};
  std::string catalog_version{};
  DebugArtifactTrigger trigger{DebugArtifactTrigger::Manual};
  std::array<common::CardSlot, common::kAugmentCardCount> card_slots{};
};

struct DebugArtifactSet final {
  std::filesystem::path raw_frame{};
  std::array<std::filesystem::path, common::kAugmentCardCount> card_rois{};
  std::filesystem::path sidecar_json{};
};

class DebugArtifactWriter final {
 public:
  explicit DebugArtifactWriter(std::filesystem::path output_root);

  [[nodiscard]] const std::filesystem::path& OutputRoot() const noexcept;
  [[nodiscard]] std::optional<DebugArtifactSet> WriteIfRequested(
      const common::Frame& frame, const DebugArtifactRequest& request,
      const IDebugArtifactPolicy& policy) const;

 private:
  std::filesystem::path output_root_{};
  mutable std::atomic<std::uint64_t> sequence_{0U};
};

}  // namespace lol_assistant::output
