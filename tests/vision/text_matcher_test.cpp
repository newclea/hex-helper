#include "lol_assistant/vision/text_matcher.h"

#include <cmath>
#include <iostream>
#include <string_view>
#include <vector>

#include "lol_assistant/vision/icon_matcher.h"

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

void TestNormalization() {
  constexpr std::string_view test = "Unicode/OCR normalization";
  CHECK(test, lol_assistant::vision::NormalizeOcrText(
                  "  ＡＤＡＰｔ！\u200b ") == "adapt");
  CHECK(test, lol_assistant::vision::NormalizeOcrText("尖端，发明家。") ==
                  "尖端发明家");
  CHECK(test,
        lol_assistant::vision::NormalizeOcrText("心 灵 凈 化") == "心灵净化");
}

void TestExactNormalizedAndFuzzy() {
  constexpr std::string_view test = "exact normalized bounded fuzzy";
  const std::vector<lol_assistant::vision::TitleCandidate> candidates{
      {"one", "尖端发明家"},
      {"two", "星界躯体"},
      {"three", "全心为你"},
      {"four", "心灵净化"}};
  const lol_assistant::vision::BoundedTextMatcher matcher;
  CHECK(test, matcher.Match("尖端发明家", candidates).kind ==
                  lol_assistant::vision::TextMatchKind::Exact);
  CHECK(test, matcher.Match(" 尖端，发明家 ", candidates).kind ==
                  lol_assistant::vision::TextMatchKind::Normalized);
  const auto traditional_variant = matcher.Match("心 灵 凈 化", candidates);
  CHECK(test, traditional_variant.kind ==
                  lol_assistant::vision::TextMatchKind::Normalized);
  CHECK(test, traditional_variant.id == "four");
  const auto fuzzy = matcher.Match("尖端发明嘉", candidates);
  CHECK(test, fuzzy.kind == lol_assistant::vision::TextMatchKind::Fuzzy);
  CHECK(test, fuzzy.id == "one");
  CHECK(test, fuzzy.margin >= 0.12F);
}

void TestUnknownNewTitleAndMarginRejection() {
  constexpr std::string_view test = "unknown/new-title rejection";
  const lol_assistant::vision::BoundedTextMatcher matcher;
  const std::vector<lol_assistant::vision::TitleCandidate> candidates{
      {"one", "星界躯体"}, {"two", "星界驱体"}};
  const auto ambiguous_fuzzy = matcher.Match("星界区体", candidates);
  CHECK(test, !ambiguous_fuzzy.matched());
  CHECK(test, ambiguous_fuzzy.reason == "fuzzy_margin_below_threshold");
  const auto new_title = matcher.Match("版本新增未知强化", candidates);
  CHECK(test, !new_title.matched());
  CHECK(test, new_title.reason == "no_candidate_within_fuzzy_bound");

  const std::vector<lol_assistant::vision::TitleCandidate> duplicate_titles{
      {"ADAPt", "物理转魔法"}, {"ARAM_ADAPt", "物理转魔法"}};
  const auto ambiguous_exact = matcher.Match("物理转魔法", duplicate_titles);
  CHECK(test, !ambiguous_exact.matched());
  CHECK(test, ambiguous_exact.reason == "ambiguous_exact_match");
  const auto ambiguous_normalized =
      matcher.Match(" 物理，转魔法 ", duplicate_titles);
  CHECK(test, !ambiguous_normalized.matched());
  CHECK(test, ambiguous_normalized.reason == "ambiguous_normalized_match");
}

void TestFuzzyMarginUsesAllUniqueIds() {
  constexpr std::string_view test = "fuzzy full-candidate unique-ID margin";
  const lol_assistant::vision::BoundedTextMatcher matcher;

  const std::vector<lol_assistant::vision::TitleCandidate> hard_bound_probe{
      {"top1", "abXXefghij"}, {"top2", "abcYYYghij"}};
  const auto ambiguous = matcher.Match("abcdefghij", hard_bound_probe);
  CHECK(test, !ambiguous.matched());
  CHECK(test, ambiguous.reason == "fuzzy_margin_below_threshold");
  CHECK(test, std::abs(ambiguous.top1_score - 0.80F) < 0.0001F);
  CHECK(test, std::abs(ambiguous.top2_score - 0.70F) < 0.0001F);
  CHECK(test, std::abs(ambiguous.margin - 0.10F) < 0.0001F);

  const std::vector<lol_assistant::vision::TitleCandidate> duplicate_id_probe{
      {"top1", "aXcdefghij"}, {"top1", "abXXefghij"}, {"top2", "abcYYYghij"}};
  const auto unique_ids = matcher.Match("abcdefghij", duplicate_id_probe);
  CHECK(test, unique_ids.matched());
  CHECK(test, unique_ids.id == "top1");
  CHECK(test, std::abs(unique_ids.top1_score - 0.90F) < 0.0001F);
  CHECK(test, std::abs(unique_ids.top2_score - 0.70F) < 0.0001F);
}

void TestContainedTitleIgnoresHudTag() {
  constexpr std::string_view test = "contained title after HUD tag";
  const std::vector<lol_assistant::vision::TitleCandidate> candidates{
      {"ARAM_SkilledSniper", "老练狙神"},
      {"ARAM_Upgrade_IE", "升级：无尽之刃"},
      {"ARAM_TankEngine", "坦克引擎"},
      {"Stat_DamagePercent", "伤害"}};
  const lol_assistant::vision::BoundedTextMatcher matcher;
  const auto tagged = matcher.Match("伤害老练狙神", candidates);
  CHECK(test, tagged.kind == lol_assistant::vision::TextMatchKind::Normalized);
  CHECK(test, tagged.id == "ARAM_SkilledSniper");
  CHECK(test, tagged.reason == "normalized_match:contained");
  const auto upgrade = matcher.Match("伤害升级：无尽之刃", candidates);
  CHECK(test, upgrade.matched());
  CHECK(test, upgrade.id == "ARAM_Upgrade_IE");
  const auto no_colon = matcher.Match("升级无尽之刃", candidates);
  CHECK(test, no_colon.matched());
  CHECK(test, no_colon.id == "ARAM_Upgrade_IE");
  const auto spaced = matcher.Match("老 练 狙 神", candidates);
  CHECK(test, spaced.matched());
  CHECK(test, spaced.id == "ARAM_SkilledSniper");
  const auto fullwidth = matcher.Match("老练狙神！！", candidates);
  CHECK(test, fullwidth.matched());
  CHECK(test, fullwidth.id == "ARAM_SkilledSniper");
}

void TestEnglishAliasAndFullwidth() {
  constexpr std::string_view test = "english alias and fullwidth";
  const std::vector<lol_assistant::vision::TitleCandidate> candidates{
      {"ARAM_ADAPt", "物理转魔法"},
      {"ARAM_ADAPt", "ADAPt"},
      {"ARAM_AllForYou", "全心为你"},
      {"ARAM_AllForYou", "All For You"},
      {"ARAM_Boomerang", "回力OK镖"}};
  const lol_assistant::vision::BoundedTextMatcher matcher;
  const auto adapt = matcher.Match("adapt", candidates);
  CHECK(test, adapt.matched());
  CHECK(test, adapt.id == "ARAM_ADAPt");
  const auto fullwidth_adapt = matcher.Match("ＡＤＡＰｔ", candidates);
  CHECK(test, fullwidth_adapt.matched());
  CHECK(test, fullwidth_adapt.id == "ARAM_ADAPt");
  const auto noisy = matcher.Match("【伤害】物理　转　魔法", candidates);
  CHECK(test, noisy.matched());
  CHECK(test, noisy.id == "ARAM_ADAPt");
  const auto english = matcher.Match("ａｌｌ ｆｏｒ ｙｏｕ", candidates);
  CHECK(test, english.matched());
  CHECK(test, english.id == "ARAM_AllForYou");
  const auto ok_boomerang = matcher.Match("回力ok镖", candidates);
  CHECK(test, ok_boomerang.matched());
  CHECK(test, ok_boomerang.id == "ARAM_Boomerang");
}

void TestChineseLibraryContains() {
  constexpr std::string_view test = "chinese-only library contains";
  const std::vector<lol_assistant::vision::TitleCandidate> candidates{
      {"ARAM_CritHeal", "会心治疗"},
      {"ARAM_DivineIntervention", "神圣干预"},
      {"ARAM_BangBang", "狙神飞星"},
      {"Stat_DamagePercent", "伤害"}};
  const lol_assistant::vision::BoundedTextMatcher matcher;
  const auto partial = matcher.Match("会心治", candidates);
  CHECK(test, partial.matched());
  CHECK(test, partial.id == "ARAM_CritHeal");
  CHECK(test, partial.reason == "normalized_match:library_contains");
  const auto mixed = matcher.Match("abc神圣干预!!!", candidates);
  CHECK(test, mixed.matched());
  CHECK(test, mixed.id == "ARAM_DivineIntervention");
  const auto tagged = matcher.Match("伤害狙神飞星", candidates);
  CHECK(test, tagged.matched());
  CHECK(test, tagged.id == "ARAM_BangBang");
  CHECK(test, !matcher.Match("伤害", candidates).matched());
  CHECK(test, !matcher.Match("功能", candidates).matched());
  CHECK(test, !matcher.Match("假海克斯乱码", candidates).matched());
  const std::vector<lol_assistant::vision::TitleCandidate> hydra{
      {"Upgrade_Ravenous", "升级：贪欲九头蛇"},
      {"Quest_UltraHydra", "终极九头蛇"}};
  const auto hydra_match = matcher.Match("终极九头蛇", hydra);
  CHECK(test, hydra_match.matched());
  CHECK(test, hydra_match.id == "Quest_UltraHydra");
}

void TestIconHashUnavailableWithoutTemplates() {
  constexpr std::string_view test = "icon hash interface without templates";
  lol_assistant::detector::OwningBgraCrop crop;
  crop.width = 9U;
  crop.height = 8U;
  crop.stride = 36U;
  crop.pixels.resize(static_cast<std::size_t>(crop.stride) * crop.height, 255U);
  const auto hash = lol_assistant::vision::ComputeDifferenceHash(crop);
  CHECK(test, hash.ok());
  const lol_assistant::vision::PerceptualHashTemplateMatcher matcher;
  const auto match = matcher.Match(crop);
  CHECK(test, !match.available());
  CHECK(test, match.reason == "template_unavailable");
  CHECK(test, !match.confidence.has_value());
}

} // namespace

int main() {
  TestNormalization();
  TestExactNormalizedAndFuzzy();
  TestUnknownNewTitleAndMarginRejection();
  TestFuzzyMarginUsesAllUniqueIds();
  TestContainedTitleIgnoresHudTag();
  TestEnglishAliasAndFullwidth();
  TestChineseLibraryContains();
  TestIconHashUnavailableWithoutTemplates();
  std::cout << "vision matcher checks=" << g_checks
            << " failures=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
