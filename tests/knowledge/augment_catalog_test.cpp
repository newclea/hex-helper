#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/vision/text_matcher.h"

#include <filesystem>
#include <iostream>
#include <string_view>

#ifndef LOL_ASSISTANT_CATALOG_PATH
#error LOL_ASSISTANT_CATALOG_PATH must be defined
#endif

namespace {

int g_failures = 0;
int g_checks = 0;

void Check(const bool condition, const std::string_view expression,
           const std::string_view test) {
  ++g_checks;
  if (!condition) {
    ++g_failures;
    std::cerr << "[FAIL] " << test << ": " << expression << '\n';
  }
}

#define CHECK(test, expression) Check((expression), #expression, (test))

void TestCatalog() {
  constexpr std::string_view test = "versioned catalog and memberships";
  const auto loaded = lol_assistant::knowledge::LoadAugmentCatalog(
      std::filesystem::path{LOL_ASSISTANT_CATALOG_PATH});
  CHECK(test, loaded.ok());
  if (!loaded.ok()) {
    std::cerr << loaded.reason << '\n';
    return;
  }
  const auto& catalog = *loaded.catalog;
  CHECK(test, catalog.schema_version() == 1U);
  CHECK(test, !catalog.catalog_version().empty());
  CHECK(test, catalog.locale() == "zh-CN");
  CHECK(test, catalog.records().size() == 655U);
  CHECK(test, catalog.RecordsForMode("CHERRY").size() == 44U);
  CHECK(test, catalog.RecordsForMode("KIWI").size() == 220U);
  CHECK(test, catalog.RecordsForMode("KIWI_JADE").size() == 188U);

  const auto* legacy = catalog.FindById("ADAPt");
  const auto* aram = catalog.FindById("ARAM_ADAPt");
  CHECK(test, legacy != nullptr && legacy->numeric_id == 205);
  CHECK(test, legacy != nullptr && legacy->modes.empty());
  CHECK(test, aram != nullptr && aram->numeric_id == 1205);
  CHECK(test, aram != nullptr && aram->IsInMode("KIWI"));
  CHECK(test, aram != nullptr && aram->IsInMode("KIWI_JADE"));
  CHECK(test, legacy != nullptr && aram != nullptr &&
                  legacy->display_name == aram->display_name);

  const lol_assistant::vision::BoundedTextMatcher matcher;
  const auto unscoped = matcher.Match(
      "物理转魔法", lol_assistant::vision::BuildTitleCandidates(catalog));
  CHECK(test, !unscoped.matched());
  CHECK(test, unscoped.reason == "ambiguous_exact_match");
  const auto kiwi = matcher.Match(
      "物理转魔法",
      lol_assistant::vision::BuildTitleCandidates(catalog, "KIWI"));
  CHECK(test, kiwi.matched());
  CHECK(test, kiwi.id == "ARAM_ADAPt");

  const auto kiwi_candidates =
      lol_assistant::vision::BuildTitleCandidates(catalog, "KIWI");
  const auto arena_candidates =
      lol_assistant::vision::BuildTitleCandidatesWithArenaNames(catalog,
                                                               "KIWI");
  CHECK(test, arena_candidates.size() > kiwi_candidates.size());
  const auto haste = matcher.Match("急急小子", arena_candidates);
  CHECK(test, haste.matched());
  CHECK(test, haste.id == "ARAM_WithHaste");

  std::string unique_title;
  for (const auto& record : catalog.records()) {
    if (record.display_name.empty() || record.IsInMode("KIWI")) {
      continue;
    }
    bool already_in_kiwi = false;
    for (const auto& candidate : kiwi_candidates) {
      if (candidate.title == record.display_name) {
        already_in_kiwi = true;
        break;
      }
    }
    if (!already_in_kiwi) {
      unique_title = record.display_name;
      break;
    }
  }
  CHECK(test, !unique_title.empty());
  const auto extra = matcher.Match(unique_title, arena_candidates);
  CHECK(test, extra.matched());
  CHECK(test, extra.title == unique_title);
}

}  // namespace

int main() {
  TestCatalog();
  std::cout << "knowledge checks=" << g_checks << " failures=" << g_failures
            << '\n';
  return g_failures == 0 ? 0 : 1;
}
