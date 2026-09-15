#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace lol_assistant::knowledge {

struct AugmentRecord final {
  std::string id{};
  std::int64_t numeric_id{0};
  std::string display_name{};
  std::string default_name{};
  std::string icon{};
  std::string rarity{};
  std::vector<std::string> modes{};

  [[nodiscard]] bool IsInMode(std::string_view mode) const noexcept;
};

class AugmentCatalog final {
 public:
  [[nodiscard]] std::uint32_t schema_version() const noexcept;
  [[nodiscard]] const std::string& catalog_version() const noexcept;
  [[nodiscard]] const std::string& locale() const noexcept;
  [[nodiscard]] const std::vector<AugmentRecord>& records() const noexcept;
  [[nodiscard]] const AugmentRecord* FindById(std::string_view id) const noexcept;
  [[nodiscard]] std::vector<const AugmentRecord*> RecordsForMode(
      std::string_view mode) const;

 private:
  friend struct CatalogLoadResult;
  friend CatalogLoadResult LoadAugmentCatalog(const std::filesystem::path& path);

  std::uint32_t schema_version_{0U};
  std::string catalog_version_{};
  std::string locale_{};
  std::vector<AugmentRecord> records_{};
};

enum class CatalogLoadStatus : std::uint8_t {
  Loaded = 0,
  IoError = 1,
  InvalidJson = 2,
  UnsupportedSchema = 3,
  InvalidCatalog = 4,
};

struct CatalogLoadResult final {
  CatalogLoadStatus status{CatalogLoadStatus::InvalidCatalog};
  std::optional<AugmentCatalog> catalog{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept {
    return status == CatalogLoadStatus::Loaded && catalog.has_value();
  }
};

[[nodiscard]] CatalogLoadResult LoadAugmentCatalog(
    const std::filesystem::path& path);

}  // namespace lol_assistant::knowledge
