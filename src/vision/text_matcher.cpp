#include "lol_assistant/vision/text_matcher.h"

#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cwctype>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace lol_assistant::vision {
namespace {

[[nodiscard]] std::wstring Utf8ToWide(const std::string_view input) {
  if (input.empty()) {
    return {};
  }
  if (input.size() >
      static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    return {};
  }
  const int input_size = static_cast<int>(input.size());
  const int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                         input.data(), input_size, nullptr, 0);
  if (length <= 0) {
    return {};
  }
  std::wstring output(static_cast<std::size_t>(length), L'\0');
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                          input_size, output.data(), length) != length) {
    return {};
  }
  return output;
}

[[nodiscard]] std::string WideToUtf8(const std::wstring_view input) {
  if (input.empty()) {
    return {};
  }
  if (input.size() >
      static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    return {};
  }
  const int input_size = static_cast<int>(input.size());
  const int length =
      WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                          input_size, nullptr, 0, nullptr, nullptr);
  if (length <= 0) {
    return {};
  }
  std::string output(static_cast<std::size_t>(length), '\0');
  if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                          input_size, output.data(), length, nullptr,
                          nullptr) != length) {
    return {};
  }
  return output;
}

[[nodiscard]] std::wstring NormalizeCompatibility(const std::wstring &input) {
  if (input.empty()) {
    return {};
  }
  const int required =
      NormalizeString(NormalizationKC, input.data(),
                      static_cast<int>(input.size()), nullptr, 0);
  if (required <= 0) {
    return input;
  }
  std::wstring output(static_cast<std::size_t>(required), L'\0');
  const int written =
      NormalizeString(NormalizationKC, input.data(),
                      static_cast<int>(input.size()), output.data(), required);
  if (written <= 0) {
    return input;
  }
  output.resize(static_cast<std::size_t>(written));
  return output;
}

[[nodiscard]] std::wstring MapSimplifiedChinese(const std::wstring &input) {
  if (input.empty()) {
    return {};
  }
  const int size = static_cast<int>(input.size());
  const int required =
      LCMapStringEx(L"zh-CN", LCMAP_SIMPLIFIED_CHINESE, input.data(), size,
                    nullptr, 0, nullptr, nullptr, 0);
  if (required <= 0) {
    return input;
  }
  std::wstring output(static_cast<std::size_t>(required), L'\0');
  if (LCMapStringEx(L"zh-CN", LCMAP_SIMPLIFIED_CHINESE, input.data(), size,
                    output.data(), required, nullptr, nullptr, 0) != required) {
    return input;
  }
  return output;
}

[[nodiscard]] bool IsIgnorable(const wchar_t value) noexcept {
  if (value == L'\u200B' || value == L'\u200C' || value == L'\u200D' ||
      value == L'\u2060' || value == L'\uFEFF') {
    return true;
  }
  WORD character_type = 0U;
  if (GetStringTypeW(CT_CTYPE1, &value, 1, &character_type) == 0) {
    return std::iswspace(value) != 0;
  }
  return (character_type & (C1_SPACE | C1_PUNCT | C1_CNTRL)) != 0U;
}

[[nodiscard]] std::wstring NormalizeWide(const std::string_view input) {
  auto normalized = NormalizeCompatibility(Utf8ToWide(input));
  if (normalized.empty()) {
    return {};
  }
  const int size = static_cast<int>(normalized.size());
  const int required =
      LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, normalized.data(),
                    size, nullptr, 0, nullptr, nullptr, 0);
  if (required > 0) {
    std::wstring lower(static_cast<std::size_t>(required), L'\0');
    if (LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, normalized.data(),
                      size, lower.data(), required, nullptr, nullptr,
                      0) == required) {
      normalized = std::move(lower);
    }
  }
  normalized = MapSimplifiedChinese(normalized);

  std::wstring compact;
  compact.reserve(normalized.size());
  for (const wchar_t value : normalized) {
    if (!IsIgnorable(value)) {
      compact.push_back(value);
    }
  }
  return compact;
}

[[nodiscard]] bool IsCjk(const wchar_t value) noexcept {
  return (value >= 0x4E00 && value <= 0x9FFF) ||
         (value >= 0x3400 && value <= 0x4DBF);
}

[[nodiscard]] std::wstring ExtractCjk(const std::wstring_view input) {
  std::wstring output;
  output.reserve(input.size());
  for (const wchar_t value : input) {
    if (IsCjk(value)) {
      output.push_back(value);
    }
  }
  return output;
}

[[nodiscard]] bool IsHudTag(const std::wstring_view text) noexcept {
  return text == L"伤害" || text == L"复原力" || text == L"复苏力" ||
         text == L"坦度" || text == L"爆发力" || text == L"机动" ||
         text == L"全能" || text == L"成长" || text == L"经济" ||
         text == L"暴击几率" || text == L"星界力" || text == L"圣毅力" ||
         text == L"任务" || text == L"功能";
}

[[nodiscard]] bool IsSubsequence(const std::wstring_view needle,
                                 const std::wstring_view haystack) noexcept {
  if (needle.empty()) {
    return false;
  }
  std::size_t index = 0U;
  for (const wchar_t value : haystack) {
    if (value == needle[index]) {
      ++index;
      if (index == needle.size()) {
        return true;
      }
    }
  }
  return false;
}

[[nodiscard]] std::size_t EditDistanceBounded(const std::wstring_view left,
                                              const std::wstring_view right,
                                              const std::size_t bound) {
  if (left.size() > right.size() + bound ||
      right.size() > left.size() + bound) {
    return bound + 1U;
  }
  std::vector<std::size_t> previous(right.size() + 1U);
  std::vector<std::size_t> current(right.size() + 1U);
  for (std::size_t column = 0U; column <= right.size(); ++column) {
    previous[column] = column;
  }
  for (std::size_t row = 1U; row <= left.size(); ++row) {
    current[0] = row;
    std::size_t row_minimum = current[0];
    for (std::size_t column = 1U; column <= right.size(); ++column) {
      const std::size_t substitution =
          previous[column - 1U] +
          static_cast<std::size_t>(left[row - 1U] != right[column - 1U]);
      current[column] = std::min(
          {previous[column] + 1U, current[column - 1U] + 1U, substitution});
      row_minimum = std::min(row_minimum, current[column]);
    }
    if (row_minimum > bound) {
      return bound + 1U;
    }
    previous.swap(current);
  }
  return previous[right.size()];
}

struct ScoredCandidate final {
  const TitleCandidate *candidate{nullptr};
  float score{0.0F};
  std::size_t distance{0U};
  std::size_t hard_bound{0U};
};

[[nodiscard]] TextMatchResult Accepted(const TextMatchKind kind,
                                       const TitleCandidate &candidate,
                                       std::string normalized, const float top1,
                                       const float top2, std::string reason) {
  return {kind, candidate.id, candidate.title, std::move(normalized),
          top1, top2,         top1 - top2,     std::move(reason)};
}

} // namespace

std::string NormalizeOcrText(const std::string_view utf8_text) {
  return WideToUtf8(NormalizeWide(utf8_text));
}

namespace {

void AppendRecordTitles(std::vector<TitleCandidate> &candidates,
                        const knowledge::AugmentRecord &record) {
  if (record.id.empty() || record.display_name.empty()) {
    return;
  }
  candidates.push_back({record.id, record.display_name});
  if (!record.default_name.empty() &&
      record.default_name != record.display_name) {
    candidates.push_back({record.id, record.default_name});
  }
}

}  // namespace

std::vector<TitleCandidate>
BuildTitleCandidates(const knowledge::AugmentCatalog &catalog,
                     const std::optional<std::string_view> mode) {
  std::vector<TitleCandidate> candidates;
  if (mode.has_value()) {
    const auto records = catalog.RecordsForMode(*mode);
    candidates.reserve(records.size() * 2U);
    for (const auto *record : records) {
      AppendRecordTitles(candidates, *record);
    }
    return candidates;
  }
  candidates.reserve(catalog.records().size() * 2U);
  for (const auto &record : catalog.records()) {
    AppendRecordTitles(candidates, record);
  }
  return candidates;
}

namespace {

[[nodiscard]] bool IsSwarmStatId(const std::string_view augment_id) noexcept {
  return augment_id.rfind("Stat_", 0) == 0;
}

[[nodiscard]] int ArenaNameRank(
    const knowledge::AugmentRecord &record) noexcept {
  if (record.IsInMode("KIWI") || record.IsInMode("KIWI_JADE")) {
    return 4;
  }
  if (record.id.rfind("ARAM_", 0) == 0) {
    return 3;
  }
  if (record.IsInMode("CHERRY")) {
    return 2;
  }
  if (!record.modes.empty()) {
    return 1;
  }
  return 0;
}

}  // namespace

std::vector<TitleCandidate> BuildTitleCandidatesWithArenaNames(
    const knowledge::AugmentCatalog &catalog,
    const std::string_view primary_mode) {
  auto candidates = BuildTitleCandidates(catalog, primary_mode);
  std::set<std::string, std::less<>> titles;
  for (const auto &candidate : candidates) {
    titles.insert(candidate.title);
  }
  std::map<std::string, const knowledge::AugmentRecord *, std::less<>> extras;
  for (const auto &record : catalog.records()) {
    if (record.display_name.empty() || IsSwarmStatId(record.id) ||
        titles.contains(record.display_name)) {
      continue;
    }
    const auto existing = extras.find(record.display_name);
    if (existing == extras.end() ||
        ArenaNameRank(record) > ArenaNameRank(*existing->second) ||
        (ArenaNameRank(record) == ArenaNameRank(*existing->second) &&
         record.id < existing->second->id)) {
      extras[record.display_name] = &record;
    }
  }
  candidates.reserve(candidates.size() + extras.size());
  for (const auto &[title, record] : extras) {
    candidates.push_back({record->id, record->display_name});
  }
  return candidates;
}

bool TextMatcherConfig::IsValid() const noexcept {
  return std::isfinite(maximum_edit_ratio) && maximum_edit_ratio >= 0.0F &&
         maximum_edit_ratio <= 1.0F && minimum_fuzzy_length > 0U &&
         std::isfinite(minimum_fuzzy_score) && minimum_fuzzy_score >= 0.0F &&
         minimum_fuzzy_score <= 1.0F &&
         std::isfinite(minimum_top1_top2_margin) &&
         minimum_top1_top2_margin >= 0.0F && minimum_top1_top2_margin <= 1.0F;
}

BoundedTextMatcher::BoundedTextMatcher(TextMatcherConfig config)
    : config_(config) {
  if (!config_.IsValid()) {
    throw std::invalid_argument("invalid text matcher config");
  }
}

TextMatchResult
BoundedTextMatcher::Match(const std::string_view raw_text,
                          const std::vector<TitleCandidate> &candidates) const {
  TextMatchResult unknown;
  unknown.normalized_text = NormalizeOcrText(raw_text);
  if (raw_text.empty() || unknown.normalized_text.empty()) {
    unknown.reason = "empty_text";
    return unknown;
  }
  if (candidates.empty()) {
    unknown.reason = "empty_catalog";
    return unknown;
  }
  if (IsHudTag(ExtractCjk(NormalizeWide(raw_text)))) {
    unknown.reason = "hud_tag";
    return unknown;
  }

  std::vector<const TitleCandidate *> exact;
  for (const auto &candidate : candidates) {
    if (candidate.id.empty() || candidate.title.empty()) {
      continue;
    }
    if (candidate.title == raw_text) {
      exact.push_back(&candidate);
    }
  }
  if (exact.size() == 1U) {
    return Accepted(TextMatchKind::Exact, *exact.front(),
                    std::move(unknown.normalized_text), 1.0F, 0.0F,
                    "exact_match");
  }
  if (exact.size() > 1U) {
    unknown.top1_score = 1.0F;
    unknown.top2_score = 1.0F;
    unknown.margin = 0.0F;
    unknown.reason = "ambiguous_exact_match";
    return unknown;
  }

  std::vector<const TitleCandidate *> normalized_matches;
  std::vector<std::pair<const TitleCandidate *, std::wstring>>
      normalized_candidates;
  normalized_candidates.reserve(candidates.size());
  const auto normalized_input = NormalizeWide(raw_text);
  for (const auto &candidate : candidates) {
    if (candidate.id.empty() || candidate.title.empty()) {
      continue;
    }
    auto normalized_title = NormalizeWide(candidate.title);
    if (normalized_title.empty()) {
      continue;
    }
    if (normalized_title == normalized_input) {
      normalized_matches.push_back(&candidate);
    }
    normalized_candidates.emplace_back(&candidate, std::move(normalized_title));
  }
  if (normalized_matches.size() == 1U) {
    return Accepted(TextMatchKind::Normalized, *normalized_matches.front(),
                    std::move(unknown.normalized_text), 1.0F, 0.0F,
                    "normalized_match");
  }
  if (normalized_matches.size() > 1U) {
    unknown.top1_score = 1.0F;
    unknown.top2_score = 1.0F;
    unknown.reason = "ambiguous_normalized_match";
    return unknown;
  }

  const auto chinese_input = ExtractCjk(normalized_input);
  if (!chinese_input.empty() && !IsHudTag(chinese_input)) {
    std::vector<const TitleCandidate *> chinese_exact;
    for (const auto &[candidate, normalized_title] : normalized_candidates) {
      if (ExtractCjk(normalized_title) == chinese_input) {
        const bool same_title = std::any_of(
            chinese_exact.begin(), chinese_exact.end(),
            [&chinese_input](const TitleCandidate *item) {
              return ExtractCjk(NormalizeWide(item->title)) == chinese_input;
            });
        if (!same_title) {
          chinese_exact.push_back(candidate);
        }
      }
    }
    if (chinese_exact.size() == 1U) {
      return Accepted(TextMatchKind::Normalized, *chinese_exact.front(),
                      std::move(unknown.normalized_text), 1.0F, 0.0F,
                      "normalized_match:chinese");
    }
    if (chinese_exact.size() > 1U) {
      unknown.top1_score = 1.0F;
      unknown.top2_score = 1.0F;
      unknown.reason = "ambiguous_normalized_match";
      return unknown;
    }

    // Keep only Chinese, then accept the unique official name that contains
    // that fragment ("会心治" -> 会心治疗).
    std::vector<const TitleCandidate *> library_contains;
    std::size_t library_length = 0U;
    for (const auto &[candidate, normalized_title] : normalized_candidates) {
      const auto title_zh = ExtractCjk(normalized_title);
      if (title_zh.size() < 3U || IsHudTag(title_zh) ||
          chinese_input.size() < 2U ||
          title_zh.find(chinese_input) == std::wstring::npos ||
          chinese_input.size() * 4U < title_zh.size() * 3U) {
        continue;
      }
      if (title_zh.size() > library_length) {
        library_contains.clear();
        library_contains.push_back(candidate);
        library_length = title_zh.size();
        continue;
      }
      if (title_zh.size() != library_length) {
        continue;
      }
      const bool same_title = std::any_of(
          library_contains.begin(), library_contains.end(),
          [&title_zh](const TitleCandidate *item) {
            return ExtractCjk(NormalizeWide(item->title)) == title_zh;
          });
      if (!same_title) {
        library_contains.push_back(candidate);
      }
    }
    if (library_contains.size() == 1U) {
      return Accepted(TextMatchKind::Normalized, *library_contains.front(),
                      std::move(unknown.normalized_text), 0.94F, 0.0F,
                      "normalized_match:library_contains");
    }
    if (library_contains.size() > 1U) {
      unknown.top1_score = 0.94F;
      unknown.top2_score = 0.94F;
      unknown.reason = "ambiguous_library_contains";
      return unknown;
    }
  }

  // Card OCR often prepends the HUD tag or extra glyphs. Accept the unique
  // longest official title that can be spelled, in order, from the compact
  // OCR so "伤害老练狙神" / "升级无尽之刃" still resolve to the catalog name.
  const auto contained_haystack =
      chinese_input.empty() ? normalized_input : chinese_input;
  std::vector<const TitleCandidate *> contained;
  std::size_t contained_length = 0U;
  for (const auto &[candidate, normalized_title] : normalized_candidates) {
    const auto title_zh = ExtractCjk(normalized_title);
    const auto needle = title_zh.empty() ? normalized_title : title_zh;
    if (needle.size() < 3U || !IsSubsequence(needle, contained_haystack)) {
      continue;
    }
    if (needle.size() > contained_length) {
      contained.clear();
      contained.push_back(candidate);
      contained_length = needle.size();
      continue;
    }
    if (needle.size() != contained_length) {
      continue;
    }
    const bool same_id = std::any_of(
        contained.begin(), contained.end(),
        [&candidate](const TitleCandidate *item) {
          return item->id == candidate->id;
        });
    if (!same_id) {
      contained.push_back(candidate);
    }
  }
  if (contained.size() == 1U && contained_length < contained_haystack.size()) {
    return Accepted(TextMatchKind::Normalized, *contained.front(),
                    std::move(unknown.normalized_text), 0.96F, 0.0F,
                    "normalized_match:contained");
  }
  if (contained.size() > 1U) {
    unknown.top1_score = 0.96F;
    unknown.top2_score = 0.96F;
    unknown.reason = "ambiguous_contained_match";
    return unknown;
  }
  if (normalized_input.size() < config_.minimum_fuzzy_length) {
    unknown.reason = "too_short_for_fuzzy";
    return unknown;
  }

  // Score every normalized candidate first, then aggregate by augment ID.
  // The edit hard bound gates only the global top-1; filtering before sorting
  // would hide a close top-2 and inflate the ambiguity margin.
  std::vector<ScoredCandidate> scored;
  for (const auto &[candidate, normalized_title] : normalized_candidates) {
    const std::size_t maximum_length =
        std::max(normalized_input.size(), normalized_title.size());
    if (maximum_length == 0U) {
      continue;
    }
    const auto ratio_bound = static_cast<std::size_t>(std::floor(
        static_cast<double>(maximum_length) * config_.maximum_edit_ratio));
    const std::size_t bound =
        std::min(config_.maximum_edit_distance, ratio_bound);
    const std::size_t distance =
        EditDistanceBounded(normalized_input, normalized_title, maximum_length);
    const float score = 1.0F - static_cast<float>(distance) /
                                   static_cast<float>(maximum_length);
    const auto same_id =
        std::find_if(scored.begin(), scored.end(),
                     [&candidate](const ScoredCandidate &item) {
                       return item.candidate->id == candidate->id;
                     });
    if (same_id == scored.end()) {
      scored.push_back({candidate, score, distance, bound});
    } else if (score > same_id->score ||
               (score == same_id->score &&
                candidate->title < same_id->candidate->title)) {
      *same_id = {candidate, score, distance, bound};
    }
  }
  std::sort(scored.begin(), scored.end(),
            [](const ScoredCandidate &left, const ScoredCandidate &right) {
              if (left.score != right.score) {
                return left.score > right.score;
              }
              return left.candidate->id < right.candidate->id;
            });
  if (scored.empty()) {
    unknown.reason = "no_candidate_within_fuzzy_bound";
    return unknown;
  }
  unknown.top1_score = scored[0].score;
  unknown.top2_score = scored.size() > 1U ? scored[1].score : 0.0F;
  unknown.margin = unknown.top1_score - unknown.top2_score;
  if (scored[0].distance > scored[0].hard_bound) {
    unknown.reason = "no_candidate_within_fuzzy_bound";
    return unknown;
  }
  if (unknown.top1_score < config_.minimum_fuzzy_score) {
    unknown.reason = "fuzzy_score_below_threshold";
    return unknown;
  }
  if (unknown.margin < config_.minimum_top1_top2_margin) {
    unknown.reason = "fuzzy_margin_below_threshold";
    return unknown;
  }
  return Accepted(TextMatchKind::Fuzzy, *scored[0].candidate,
                  std::move(unknown.normalized_text), unknown.top1_score,
                  unknown.top2_score, "fuzzy_match");
}

} // namespace lol_assistant::vision
