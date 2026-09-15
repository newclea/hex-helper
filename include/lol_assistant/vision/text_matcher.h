#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/knowledge/augment_catalog.h"

namespace lol_assistant::vision {

[[nodiscard]] std::string NormalizeOcrText(std::string_view utf8_text);

struct TitleCandidate final {
  std::string id{};
  std::string title{};
};

// Supplying a mode is the only supported way to narrow duplicate localized
// titles. Without a mode all records remain candidates, so the matcher can
// reject same-title/multi-ID ambiguity instead of choosing by record order.
[[nodiscard]] std::vector<TitleCandidate> BuildTitleCandidates(
    const knowledge::AugmentCatalog& catalog,
    std::optional<std::string_view> mode = std::nullopt);

// KIWI/KIWI_JADE titles stay mode-scoped so icon fusion can use ARAM_* IDs.
// Extra unique display names (empty-mode / CHERRY) are appended so Arena
// cards that share a Chinese title with ARAM still resolve, and Arena-only
// names are not dropped.
[[nodiscard]] std::vector<TitleCandidate> BuildTitleCandidatesWithArenaNames(
    const knowledge::AugmentCatalog& catalog,
    std::string_view primary_mode);

struct TextMatcherConfig final {
  std::size_t maximum_edit_distance{2U};
  float maximum_edit_ratio{0.25F};
  std::size_t minimum_fuzzy_length{4U};
  float minimum_fuzzy_score{0.72F};
  float minimum_top1_top2_margin{0.12F};

  [[nodiscard]] bool IsValid() const noexcept;
};

enum class TextMatchKind : std::uint8_t {
  Unknown = 0,
  Exact = 1,
  Normalized = 2,
  Fuzzy = 3,
};

struct TextMatchResult final {
  TextMatchKind kind{TextMatchKind::Unknown};
  std::optional<std::string> id{};
  std::optional<std::string> title{};
  std::string normalized_text{};
  float top1_score{0.0F};
  float top2_score{0.0F};
  float margin{0.0F};
  std::string reason{"unknown"};

  [[nodiscard]] bool matched() const noexcept {
    return kind != TextMatchKind::Unknown && id.has_value();
  }
};

class BoundedTextMatcher final {
 public:
  explicit BoundedTextMatcher(TextMatcherConfig config = {});

  [[nodiscard]] TextMatchResult Match(
      std::string_view raw_text,
      const std::vector<TitleCandidate>& candidates) const;

 private:
  TextMatcherConfig config_{};
};

}  // namespace lol_assistant::vision
