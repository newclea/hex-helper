#include "lol_assistant/knowledge/augment_catalog.h"

#include <algorithm>
#include <charconv>
#include <cstddef>
#include <fstream>
#include <iterator>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <system_error>
#include <utility>

namespace lol_assistant::knowledge {
namespace {

enum class JsonKind : std::uint8_t {
  Null,
  Boolean,
  Integer,
  String,
  Array,
  Object,
};

struct JsonValue final {
  JsonKind kind{JsonKind::Null};
  bool boolean{false};
  std::int64_t integer{0};
  std::string string{};
  std::vector<JsonValue> array{};
  std::map<std::string, JsonValue, std::less<>> object{};

  [[nodiscard]] const JsonValue* Find(const std::string_view key) const noexcept {
    if (kind != JsonKind::Object) {
      return nullptr;
    }
    const auto iterator = object.find(key);
    return iterator == object.end() ? nullptr : &iterator->second;
  }
};

class JsonParser final {
 public:
  explicit JsonParser(std::string_view input) : input_(input) {}

  [[nodiscard]] JsonValue Parse() {
    auto value = ParseValue(0U);
    SkipWhitespace();
    if (position_ != input_.size()) {
      Error("trailing content");
    }
    return value;
  }

 private:
  static constexpr std::size_t kMaximumDepth = 64U;

  [[noreturn]] void Error(const std::string_view message) const {
    throw std::runtime_error(std::string{message} + " at byte " +
                             std::to_string(position_));
  }

  void SkipWhitespace() noexcept {
    while (position_ < input_.size()) {
      const char value = input_[position_];
      if (value != ' ' && value != '\t' && value != '\r' && value != '\n') {
        break;
      }
      ++position_;
    }
  }

  [[nodiscard]] char Take() {
    if (position_ >= input_.size()) {
      Error("unexpected end of input");
    }
    return input_[position_++];
  }

  void Expect(const char expected) {
    if (Take() != expected) {
      Error("unexpected token");
    }
  }

  [[nodiscard]] JsonValue ParseValue(const std::size_t depth) {
    if (depth > kMaximumDepth) {
      Error("maximum nesting depth exceeded");
    }
    SkipWhitespace();
    if (position_ >= input_.size()) {
      Error("missing value");
    }
    switch (input_[position_]) {
      case '{':
        return ParseObject(depth + 1U);
      case '[':
        return ParseArray(depth + 1U);
      case '"': {
        JsonValue value;
        value.kind = JsonKind::String;
        value.string = ParseString();
        return value;
      }
      case 't':
        return ParseLiteral("true", JsonKind::Boolean, true);
      case 'f':
        return ParseLiteral("false", JsonKind::Boolean, false);
      case 'n':
        return ParseLiteral("null", JsonKind::Null, false);
      default:
        return ParseInteger();
    }
  }

  [[nodiscard]] JsonValue ParseLiteral(const std::string_view literal,
                                       const JsonKind kind,
                                       const bool boolean) {
    if (input_.substr(position_, literal.size()) != literal) {
      Error("invalid literal");
    }
    position_ += literal.size();
    JsonValue value;
    value.kind = kind;
    value.boolean = boolean;
    return value;
  }

  [[nodiscard]] static std::uint32_t HexDigit(const char value) {
    if (value >= '0' && value <= '9') {
      return static_cast<std::uint32_t>(value - '0');
    }
    if (value >= 'a' && value <= 'f') {
      return static_cast<std::uint32_t>(value - 'a' + 10);
    }
    if (value >= 'A' && value <= 'F') {
      return static_cast<std::uint32_t>(value - 'A' + 10);
    }
    throw std::runtime_error("invalid hexadecimal digit");
  }

  [[nodiscard]] std::uint32_t ParseHexCodeUnit() {
    std::uint32_t value = 0U;
    for (std::size_t index = 0U; index < 4U; ++index) {
      if (position_ >= input_.size()) {
        Error("incomplete unicode escape");
      }
      try {
        value = value * 16U + HexDigit(input_[position_++]);
      } catch (const std::runtime_error&) {
        Error("invalid unicode escape");
      }
    }
    return value;
  }

  static void AppendUtf8(std::string& output, const std::uint32_t code_point) {
    if (code_point <= 0x7FU) {
      output.push_back(static_cast<char>(code_point));
    } else if (code_point <= 0x7FFU) {
      output.push_back(static_cast<char>(0xC0U | (code_point >> 6U)));
      output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    } else if (code_point <= 0xFFFFU) {
      output.push_back(static_cast<char>(0xE0U | (code_point >> 12U)));
      output.push_back(
          static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
      output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    } else {
      output.push_back(static_cast<char>(0xF0U | (code_point >> 18U)));
      output.push_back(
          static_cast<char>(0x80U | ((code_point >> 12U) & 0x3FU)));
      output.push_back(
          static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
      output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    }
  }

  [[nodiscard]] std::string ParseString() {
    Expect('"');
    std::string output;
    while (position_ < input_.size()) {
      const char value = Take();
      if (value == '"') {
        return output;
      }
      if (static_cast<unsigned char>(value) < 0x20U) {
        Error("control character in string");
      }
      if (value != '\\') {
        output.push_back(value);
        continue;
      }
      const char escape = Take();
      switch (escape) {
        case '"':
        case '\\':
        case '/':
          output.push_back(escape);
          break;
        case 'b':
          output.push_back('\b');
          break;
        case 'f':
          output.push_back('\f');
          break;
        case 'n':
          output.push_back('\n');
          break;
        case 'r':
          output.push_back('\r');
          break;
        case 't':
          output.push_back('\t');
          break;
        case 'u': {
          std::uint32_t code_point = ParseHexCodeUnit();
          if (code_point >= 0xD800U && code_point <= 0xDBFFU) {
            if (position_ + 2U > input_.size() || input_[position_] != '\\' ||
                input_[position_ + 1U] != 'u') {
              Error("missing low surrogate");
            }
            position_ += 2U;
            const std::uint32_t low = ParseHexCodeUnit();
            if (low < 0xDC00U || low > 0xDFFFU) {
              Error("invalid low surrogate");
            }
            code_point = 0x10000U + ((code_point - 0xD800U) << 10U) +
                         (low - 0xDC00U);
          } else if (code_point >= 0xDC00U && code_point <= 0xDFFFU) {
            Error("unexpected low surrogate");
          }
          AppendUtf8(output, code_point);
          break;
        }
        default:
          Error("invalid string escape");
      }
    }
    Error("unterminated string");
  }

  [[nodiscard]] JsonValue ParseInteger() {
    const std::size_t start = position_;
    if (input_[position_] == '-') {
      ++position_;
    }
    if (position_ >= input_.size() || input_[position_] < '0' ||
        input_[position_] > '9') {
      Error("invalid integer");
    }
    if (input_[position_] == '0') {
      ++position_;
    } else {
      while (position_ < input_.size() && input_[position_] >= '0' &&
             input_[position_] <= '9') {
        ++position_;
      }
    }
    if (position_ < input_.size() &&
        (input_[position_] == '.' || input_[position_] == 'e' ||
         input_[position_] == 'E')) {
      Error("non-integer number is unsupported");
    }
    std::int64_t parsed = 0;
    const char* begin = input_.data() + start;
    const char* end = input_.data() + position_;
    const auto result = std::from_chars(begin, end, parsed);
    if (result.ec != std::errc{} || result.ptr != end) {
      Error("integer out of range");
    }
    JsonValue value;
    value.kind = JsonKind::Integer;
    value.integer = parsed;
    return value;
  }

  [[nodiscard]] JsonValue ParseArray(const std::size_t depth) {
    Expect('[');
    JsonValue value;
    value.kind = JsonKind::Array;
    SkipWhitespace();
    if (position_ < input_.size() && input_[position_] == ']') {
      ++position_;
      return value;
    }
    while (true) {
      value.array.push_back(ParseValue(depth));
      SkipWhitespace();
      const char delimiter = Take();
      if (delimiter == ']') {
        return value;
      }
      if (delimiter != ',') {
        Error("expected array delimiter");
      }
    }
  }

  [[nodiscard]] JsonValue ParseObject(const std::size_t depth) {
    Expect('{');
    JsonValue value;
    value.kind = JsonKind::Object;
    SkipWhitespace();
    if (position_ < input_.size() && input_[position_] == '}') {
      ++position_;
      return value;
    }
    while (true) {
      SkipWhitespace();
      if (position_ >= input_.size() || input_[position_] != '"') {
        Error("expected object key");
      }
      auto key = ParseString();
      SkipWhitespace();
      Expect(':');
      auto [iterator, inserted] =
          value.object.emplace(std::move(key), ParseValue(depth));
      static_cast<void>(iterator);
      if (!inserted) {
        Error("duplicate object key");
      }
      SkipWhitespace();
      const char delimiter = Take();
      if (delimiter == '}') {
        return value;
      }
      if (delimiter != ',') {
        Error("expected object delimiter");
      }
    }
  }

  std::string_view input_{};
  std::size_t position_{0U};
};

[[nodiscard]] const JsonValue& Require(const JsonValue& object,
                                       const std::string_view key,
                                       const JsonKind kind) {
  const auto* value = object.Find(key);
  if (value == nullptr || value->kind != kind) {
    throw std::runtime_error("missing or invalid field: " + std::string{key});
  }
  return *value;
}

[[nodiscard]] AugmentRecord ParseRecord(const JsonValue& value) {
  if (value.kind != JsonKind::Object) {
    throw std::runtime_error("augment record must be an object");
  }
  AugmentRecord record;
  record.id = Require(value, "id", JsonKind::String).string;
  record.numeric_id = Require(value, "numeric_id", JsonKind::Integer).integer;
  record.display_name = Require(value, "display_name", JsonKind::String).string;
  record.default_name = Require(value, "default_name", JsonKind::String).string;
  record.icon = Require(value, "icon", JsonKind::String).string;
  record.rarity = Require(value, "rarity", JsonKind::String).string;
  const auto& modes = Require(value, "modes", JsonKind::Array).array;
  std::set<std::string, std::less<>> seen_modes;
  for (const auto& mode : modes) {
    if (mode.kind != JsonKind::String || mode.string.empty() ||
        !seen_modes.insert(mode.string).second) {
      throw std::runtime_error("invalid or duplicate mode membership");
    }
    record.modes.push_back(mode.string);
  }
  if (record.id.empty() || record.display_name.empty() ||
      record.default_name.empty() || record.icon.empty() || record.rarity.empty()) {
    throw std::runtime_error("augment contains an empty required field");
  }
  return record;
}

}  // namespace

bool AugmentRecord::IsInMode(const std::string_view mode) const noexcept {
  return std::find(modes.begin(), modes.end(), mode) != modes.end();
}

std::uint32_t AugmentCatalog::schema_version() const noexcept {
  return schema_version_;
}

const std::string& AugmentCatalog::catalog_version() const noexcept {
  return catalog_version_;
}

const std::string& AugmentCatalog::locale() const noexcept { return locale_; }

const std::vector<AugmentRecord>& AugmentCatalog::records() const noexcept {
  return records_;
}

const AugmentRecord* AugmentCatalog::FindById(
    const std::string_view id) const noexcept {
  const auto iterator = std::lower_bound(
      records_.begin(), records_.end(), id,
      [](const AugmentRecord& record, const std::string_view key) {
        return record.id < key;
      });
  return iterator != records_.end() && iterator->id == id ? &*iterator : nullptr;
}

std::vector<const AugmentRecord*> AugmentCatalog::RecordsForMode(
    const std::string_view mode) const {
  std::vector<const AugmentRecord*> result;
  for (const auto& record : records_) {
    if (record.IsInMode(mode)) {
      result.push_back(&record);
    }
  }
  return result;
}

CatalogLoadResult LoadAugmentCatalog(const std::filesystem::path& path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) {
    return {CatalogLoadStatus::IoError, std::nullopt,
            "cannot_open_catalog"};
  }
  const std::string input{std::istreambuf_iterator<char>{stream},
                          std::istreambuf_iterator<char>{}};
  if (!stream.good() && !stream.eof()) {
    return {CatalogLoadStatus::IoError, std::nullopt,
            "cannot_read_catalog"};
  }

  JsonValue root;
  try {
    root = JsonParser{input}.Parse();
  } catch (const std::exception& exc) {
    return {CatalogLoadStatus::InvalidJson, std::nullopt, exc.what()};
  }
  try {
    if (root.kind != JsonKind::Object) {
      throw std::runtime_error("catalog root must be an object");
    }
    const auto schema = Require(root, "schema_version", JsonKind::Integer).integer;
    if (schema != 1) {
      return {CatalogLoadStatus::UnsupportedSchema, std::nullopt,
              "unsupported_schema_version"};
    }

    AugmentCatalog catalog;
    catalog.schema_version_ = 1U;
    catalog.catalog_version_ =
        Require(root, "catalog_version", JsonKind::String).string;
    catalog.locale_ = Require(root, "locale", JsonKind::String).string;
    const auto& augments = Require(root, "augments", JsonKind::Array).array;
    catalog.records_.reserve(augments.size());
    std::set<std::string, std::less<>> ids;
    for (const auto& value : augments) {
      auto record = ParseRecord(value);
      if (!ids.insert(record.id).second) {
        throw std::runtime_error("duplicate augment id: " + record.id);
      }
      catalog.records_.push_back(std::move(record));
    }
    if (catalog.catalog_version_.empty() || catalog.locale_.empty() ||
        catalog.records_.empty()) {
      throw std::runtime_error("catalog metadata or records are empty");
    }
    std::sort(catalog.records_.begin(), catalog.records_.end(),
              [](const AugmentRecord& left, const AugmentRecord& right) {
                return left.id < right.id;
              });
    return {CatalogLoadStatus::Loaded, std::move(catalog), "loaded"};
  } catch (const std::exception& exc) {
    return {CatalogLoadStatus::InvalidCatalog, std::nullopt, exc.what()};
  }
}

}  // namespace lol_assistant::knowledge
