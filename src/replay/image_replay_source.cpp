#include "lol_assistant/replay/image_replay_source.h"

#include <Windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cwctype>
#include <fstream>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "lol_assistant/replay/wic_image_codec.h"

namespace lol_assistant::replay {
namespace {

struct ManifestEntry final {
  std::string path{};
  std::optional<std::uint32_t> width{};
  std::optional<std::uint32_t> height{};
  std::optional<std::string> sha256{};
};

[[nodiscard]] std::string PathToUtf8(const std::filesystem::path& path) {
  const auto encoded = path.u8string();
  return {reinterpret_cast<const char*>(encoded.data()), encoded.size()};
}

[[nodiscard]] std::filesystem::path Utf8ToPath(const std::string_view value) {
  const std::u8string encoded(reinterpret_cast<const char8_t*>(value.data()),
                              value.size());
  return std::filesystem::path(encoded);
}

[[nodiscard]] bool IsSupportedImage(const std::filesystem::path& path) {
  std::wstring extension = path.extension().wstring();
  std::transform(extension.begin(), extension.end(), extension.begin(),
                 [](const wchar_t character) {
                   return static_cast<wchar_t>(std::towlower(character));
                 });
  return extension == L".png" || extension == L".jpg" ||
         extension == L".jpeg";
}

void RequireSupportedImage(const std::filesystem::path& path) {
  if (!std::filesystem::is_regular_file(path)) {
    throw ReplayError("Replay image does not exist or is not a regular file: " +
                      path.string());
  }
  if (!IsSupportedImage(path)) {
    throw ReplayError("Unsupported replay image extension: " + path.string());
  }
}

[[nodiscard]] int CompareNatural(const std::wstring_view left,
                                 const std::wstring_view right) {
  std::size_t left_index = 0U;
  std::size_t right_index = 0U;
  while (left_index < left.size() && right_index < right.size()) {
    const bool left_digit = std::iswdigit(left[left_index]) != 0;
    const bool right_digit = std::iswdigit(right[right_index]) != 0;
    if (left_digit && right_digit) {
      const std::size_t left_run_begin = left_index;
      const std::size_t right_run_begin = right_index;
      while (left_index < left.size() &&
             std::iswdigit(left[left_index]) != 0) {
        ++left_index;
      }
      while (right_index < right.size() &&
             std::iswdigit(right[right_index]) != 0) {
        ++right_index;
      }

      std::size_t left_significant = left_run_begin;
      std::size_t right_significant = right_run_begin;
      while (left_significant + 1U < left_index &&
             left[left_significant] == L'0') {
        ++left_significant;
      }
      while (right_significant + 1U < right_index &&
             right[right_significant] == L'0') {
        ++right_significant;
      }

      const std::size_t left_digits = left_index - left_significant;
      const std::size_t right_digits = right_index - right_significant;
      if (left_digits != right_digits) {
        return left_digits < right_digits ? -1 : 1;
      }
      const int numeric_compare = left.substr(left_significant, left_digits)
                                      .compare(right.substr(right_significant,
                                                            right_digits));
      if (numeric_compare != 0) {
        return numeric_compare < 0 ? -1 : 1;
      }

      const std::size_t left_run_length = left_index - left_run_begin;
      const std::size_t right_run_length = right_index - right_run_begin;
      if (left_run_length != right_run_length) {
        return left_run_length < right_run_length ? -1 : 1;
      }
      continue;
    }

    const wchar_t left_folded =
        static_cast<wchar_t>(std::towlower(left[left_index]));
    const wchar_t right_folded =
        static_cast<wchar_t>(std::towlower(right[right_index]));
    if (left_folded != right_folded) {
      return left_folded < right_folded ? -1 : 1;
    }
    ++left_index;
    ++right_index;
  }
  if (left_index != left.size()) {
    return 1;
  }
  if (right_index != right.size()) {
    return -1;
  }
  const int exact_compare = left.compare(right);
  return exact_compare < 0 ? -1 : (exact_compare > 0 ? 1 : 0);
}

class JsonLineReader final {
 public:
  JsonLineReader(std::string_view input, const std::size_t line_number)
      : input_(input), line_number_(line_number) {}

  [[nodiscard]] ManifestEntry ParseManifestEntry() {
    ManifestEntry entry;
    bool saw_path = false;
    bool saw_width = false;
    bool saw_height = false;
    bool saw_sha256 = false;

    SkipWhitespace();
    Expect('{');
    SkipWhitespace();
    if (Consume('}')) {
      Fail("manifest object must contain path");
    }

    while (true) {
      const std::string key = ParseString();
      SkipWhitespace();
      Expect(':');
      SkipWhitespace();

      if (key == "path") {
        RejectDuplicate(saw_path, key);
        saw_path = true;
        entry.path = ParseString();
      } else if (key == "width") {
        RejectDuplicate(saw_width, key);
        saw_width = true;
        entry.width = ParseOptionalUint32(key);
      } else if (key == "height") {
        RejectDuplicate(saw_height, key);
        saw_height = true;
        entry.height = ParseOptionalUint32(key);
      } else if (key == "sha256") {
        RejectDuplicate(saw_sha256, key);
        saw_sha256 = true;
        if (StartsWith("null")) {
          ConsumeLiteral("null");
        } else {
          entry.sha256 = ParseString();
        }
      } else {
        SkipValue();
      }

      SkipWhitespace();
      if (Consume('}')) {
        break;
      }
      Expect(',');
      SkipWhitespace();
    }

    SkipWhitespace();
    if (position_ != input_.size()) {
      Fail("trailing data after manifest object");
    }
    if (!saw_path || entry.path.empty()) {
      Fail("path must be a non-empty string");
    }
    return entry;
  }

 private:
  [[noreturn]] void Fail(const std::string& message) const {
    throw ReplayError("Manifest line " + std::to_string(line_number_) +
                      ": " + message);
  }

  void SkipWhitespace() {
    while (position_ < input_.size() &&
           std::isspace(static_cast<unsigned char>(input_[position_])) != 0) {
      ++position_;
    }
  }

  [[nodiscard]] bool Consume(const char expected) {
    if (position_ < input_.size() && input_[position_] == expected) {
      ++position_;
      return true;
    }
    return false;
  }

  void Expect(const char expected) {
    if (!Consume(expected)) {
      Fail(std::string("expected '") + expected + "'");
    }
  }

  [[nodiscard]] bool StartsWith(const std::string_view literal) const {
    return input_.substr(position_, literal.size()) == literal;
  }

  void ConsumeLiteral(const std::string_view literal) {
    if (!StartsWith(literal)) {
      Fail("invalid JSON literal");
    }
    position_ += literal.size();
  }

  [[nodiscard]] static int HexValue(const char value) noexcept {
    if (value >= '0' && value <= '9') {
      return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
      return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
      return value - 'A' + 10;
    }
    return -1;
  }

  [[nodiscard]] std::uint32_t ParseHex4() {
    if (input_.size() - position_ < 4U) {
      Fail("truncated unicode escape");
    }
    std::uint32_t value = 0U;
    for (std::size_t index = 0U; index < 4U; ++index) {
      const int nibble = HexValue(input_[position_++]);
      if (nibble < 0) {
        Fail("invalid unicode escape");
      }
      value = value * 16U + static_cast<std::uint32_t>(nibble);
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
      const unsigned char value =
          static_cast<unsigned char>(input_[position_++]);
      if (value == '"') {
        return output;
      }
      if (value < 0x20U) {
        Fail("unescaped control character in string");
      }
      if (value != '\\') {
        output.push_back(static_cast<char>(value));
        continue;
      }

      if (position_ >= input_.size()) {
        Fail("truncated string escape");
      }
      const char escaped = input_[position_++];
      switch (escaped) {
        case '"':
        case '\\':
        case '/':
          output.push_back(escaped);
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
          std::uint32_t code_point = ParseHex4();
          if (code_point >= 0xD800U && code_point <= 0xDBFFU) {
            if (input_.size() - position_ < 6U ||
                input_[position_] != '\\' || input_[position_ + 1U] != 'u') {
              Fail("high surrogate is not followed by a low surrogate");
            }
            position_ += 2U;
            const std::uint32_t low = ParseHex4();
            if (low < 0xDC00U || low > 0xDFFFU) {
              Fail("invalid low surrogate");
            }
            code_point = 0x10000U + ((code_point - 0xD800U) << 10U) +
                         (low - 0xDC00U);
          } else if (code_point >= 0xDC00U && code_point <= 0xDFFFU) {
            Fail("unexpected low surrogate");
          }
          AppendUtf8(output, code_point);
          break;
        }
        default:
          Fail("invalid string escape");
      }
    }
    Fail("unterminated string");
  }

  [[nodiscard]] std::optional<std::uint32_t> ParseOptionalUint32(
      const std::string& key) {
    if (StartsWith("null")) {
      ConsumeLiteral("null");
      return std::nullopt;
    }
    const std::size_t begin = position_;
    while (position_ < input_.size() && input_[position_] >= '0' &&
           input_[position_] <= '9') {
      ++position_;
    }
    if (begin == position_) {
      Fail(key + " must be an unsigned integer or null");
    }
    std::uint32_t value = 0U;
    const auto result = std::from_chars(input_.data() + begin,
                                        input_.data() + position_, value);
    if (result.ec != std::errc{} || result.ptr != input_.data() + position_ ||
        value == 0U) {
      Fail(key + " must be a positive 32-bit integer");
    }
    return value;
  }

  void SkipNumber() {
    const std::size_t begin = position_;
    if (Consume('-')) {
      // Sign consumed.
    }
    if (Consume('0')) {
      // A leading zero must stand alone before a fraction or exponent.
    } else {
      if (position_ >= input_.size() || input_[position_] < '1' ||
          input_[position_] > '9') {
        Fail("invalid JSON number");
      }
      while (position_ < input_.size() && input_[position_] >= '0' &&
             input_[position_] <= '9') {
        ++position_;
      }
    }
    if (Consume('.')) {
      const std::size_t fraction_begin = position_;
      while (position_ < input_.size() && input_[position_] >= '0' &&
             input_[position_] <= '9') {
        ++position_;
      }
      if (fraction_begin == position_) {
        Fail("invalid JSON fraction");
      }
    }
    if (position_ < input_.size() &&
        (input_[position_] == 'e' || input_[position_] == 'E')) {
      ++position_;
      if (position_ < input_.size() &&
          (input_[position_] == '+' || input_[position_] == '-')) {
        ++position_;
      }
      const std::size_t exponent_begin = position_;
      while (position_ < input_.size() && input_[position_] >= '0' &&
             input_[position_] <= '9') {
        ++position_;
      }
      if (exponent_begin == position_) {
        Fail("invalid JSON exponent");
      }
    }
    if (begin == position_) {
      Fail("invalid JSON number");
    }
  }

  void SkipValue() {
    SkipWhitespace();
    if (position_ >= input_.size()) {
      Fail("missing JSON value");
    }
    if (input_[position_] == '"') {
      static_cast<void>(ParseString());
      return;
    }
    if (Consume('{')) {
      SkipWhitespace();
      if (Consume('}')) {
        return;
      }
      while (true) {
        static_cast<void>(ParseString());
        SkipWhitespace();
        Expect(':');
        SkipValue();
        SkipWhitespace();
        if (Consume('}')) {
          return;
        }
        Expect(',');
        SkipWhitespace();
      }
    }
    if (Consume('[')) {
      SkipWhitespace();
      if (Consume(']')) {
        return;
      }
      while (true) {
        SkipValue();
        SkipWhitespace();
        if (Consume(']')) {
          return;
        }
        Expect(',');
        SkipWhitespace();
      }
    }
    if (StartsWith("true")) {
      ConsumeLiteral("true");
      return;
    }
    if (StartsWith("false")) {
      ConsumeLiteral("false");
      return;
    }
    if (StartsWith("null")) {
      ConsumeLiteral("null");
      return;
    }
    SkipNumber();
  }

  void RejectDuplicate(const bool already_seen, const std::string& key) const {
    if (already_seen) {
      Fail("duplicate field '" + key + "'");
    }
  }

  std::string_view input_;
  std::size_t line_number_{0U};
  std::size_t position_{0U};
};

class BCryptHashResources final {
 public:
  ~BCryptHashResources() {
    if (hash_ != nullptr) {
      BCryptDestroyHash(hash_);
    }
    if (algorithm_ != nullptr) {
      BCryptCloseAlgorithmProvider(algorithm_, 0U);
    }
  }

  BCRYPT_ALG_HANDLE algorithm_{nullptr};
  BCRYPT_HASH_HANDLE hash_{nullptr};
};

void CheckNtStatus(const NTSTATUS status, const char* operation) {
  if (status < 0) {
    std::ostringstream message;
    message << operation << " failed with NTSTATUS 0x" << std::hex
            << static_cast<unsigned long>(status);
    throw ReplayError(message.str());
  }
}

[[nodiscard]] std::string ComputeSha256(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw ReplayError("Unable to open image for SHA-256: " + path.string());
  }

  BCryptHashResources resources;
  CheckNtStatus(BCryptOpenAlgorithmProvider(
                    &resources.algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0U),
                "BCryptOpenAlgorithmProvider");

  DWORD object_size = 0U;
  DWORD bytes_copied = 0U;
  CheckNtStatus(BCryptGetProperty(
                    resources.algorithm_, BCRYPT_OBJECT_LENGTH,
                    reinterpret_cast<PUCHAR>(&object_size), sizeof(object_size),
                    &bytes_copied, 0U),
                "BCryptGetProperty(BCRYPT_OBJECT_LENGTH)");
  std::vector<UCHAR> hash_object(object_size);
  CheckNtStatus(BCryptCreateHash(resources.algorithm_, &resources.hash_,
                                 hash_object.data(), object_size, nullptr, 0U,
                                 0U),
                "BCryptCreateHash");

  std::array<char, 64U * 1024U> buffer{};
  while (input) {
    input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
    const std::streamsize count = input.gcount();
    if (count > 0) {
      CheckNtStatus(
          BCryptHashData(resources.hash_,
                         reinterpret_cast<PUCHAR>(buffer.data()),
                         static_cast<ULONG>(count), 0U),
          "BCryptHashData");
    }
  }
  if (!input.eof()) {
    throw ReplayError("Failed while reading image for SHA-256: " +
                      path.string());
  }

  std::array<UCHAR, 32U> digest{};
  CheckNtStatus(BCryptFinishHash(resources.hash_, digest.data(),
                                 static_cast<ULONG>(digest.size()), 0U),
                "BCryptFinishHash");
  constexpr char hex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(digest.size() * 2U);
  for (const UCHAR byte : digest) {
    encoded.push_back(hex[(byte >> 4U) & 0x0FU]);
    encoded.push_back(hex[byte & 0x0FU]);
  }
  return encoded;
}

[[nodiscard]] std::string NormalizeSha256(std::string value,
                                          const std::size_t line_number) {
  if (value.size() != 64U) {
    throw ReplayError("Manifest line " + std::to_string(line_number) +
                      ": sha256 must contain exactly 64 hexadecimal digits");
  }
  for (char& character : value) {
    if (std::isxdigit(static_cast<unsigned char>(character)) == 0) {
      throw ReplayError("Manifest line " + std::to_string(line_number) +
                        ": sha256 contains a non-hexadecimal character");
    }
    character = static_cast<char>(
        std::tolower(static_cast<unsigned char>(character)));
  }
  return value;
}

[[nodiscard]] std::vector<ManifestEntry> ReadManifest(
    const std::filesystem::path& manifest_path) {
  if (!std::filesystem::is_regular_file(manifest_path)) {
    throw ReplayError("Manifest does not exist or is not a regular file: " +
                      manifest_path.string());
  }
  std::ifstream input(manifest_path, std::ios::binary);
  if (!input) {
    throw ReplayError("Unable to open replay manifest: " +
                      manifest_path.string());
  }

  std::vector<ManifestEntry> entries;
  std::string line;
  std::size_t line_number = 0U;
  while (std::getline(input, line)) {
    ++line_number;
    if (!line.empty() && line.back() == '\r') {
      line.pop_back();
    }
    const bool all_whitespace =
        std::all_of(line.begin(), line.end(), [](const char character) {
          return std::isspace(static_cast<unsigned char>(character)) != 0;
        });
    if (all_whitespace) {
      continue;
    }
    entries.push_back(JsonLineReader(line, line_number).ParseManifestEntry());
  }
  if (!input.eof()) {
    throw ReplayError("Failed while reading replay manifest: " +
                      manifest_path.string());
  }
  if (entries.empty()) {
    throw ReplayError("Replay manifest contains no entries: " +
                      manifest_path.string());
  }
  return entries;
}

[[nodiscard]] std::vector<std::filesystem::path> ListDirectoryImages(
    const std::filesystem::path& directory) {
  if (!std::filesystem::is_directory(directory)) {
    throw ReplayError("Replay directory does not exist: " +
                      directory.string());
  }
  std::vector<std::filesystem::path> images;
  for (const auto& entry : std::filesystem::directory_iterator(directory)) {
    if (entry.is_regular_file() && IsSupportedImage(entry.path())) {
      images.push_back(entry.path());
    }
  }
  std::sort(images.begin(), images.end(),
            [](const std::filesystem::path& left,
               const std::filesystem::path& right) {
              return CompareNatural(left.filename().wstring(),
                                    right.filename().wstring()) < 0;
            });
  if (images.empty()) {
    throw ReplayError("Replay directory contains no PNG/JPEG images: " +
                      directory.string());
  }
  return images;
}

}  // namespace

ImageReplaySource::ImageReplaySource(std::filesystem::path input,
                                     const ReplayInputKind kind,
                                     const ReplayOpenOptions options)
    : input_(std::filesystem::absolute(std::move(input)).lexically_normal()),
      source_{common::FrameSourceKind::Replay, PathToUtf8(input_)},
      loop_(options.loop) {
  try {
    std::vector<std::filesystem::path> paths;
    std::vector<ManifestEntry> manifest_entries;
    switch (kind) {
      case ReplayInputKind::SingleFile:
        RequireSupportedImage(input_);
        paths.push_back(input_);
        break;
      case ReplayInputKind::Directory:
        paths = ListDirectoryImages(input_);
        break;
      case ReplayInputKind::ManifestJsonLines:
        manifest_entries = ReadManifest(input_);
        paths.reserve(manifest_entries.size());
        for (const auto& entry : manifest_entries) {
          const std::filesystem::path entry_path = Utf8ToPath(entry.path);
          paths.push_back(entry_path.is_absolute()
                              ? entry_path.lexically_normal()
                              : (input_.parent_path() / entry_path)
                                    .lexically_normal());
        }
        break;
      default:
        throw ReplayError("Unknown replay input kind");
    }

    frames_.reserve(paths.size());
    for (std::size_t index = 0U; index < paths.size(); ++index) {
      RequireSupportedImage(paths[index]);
      if (!manifest_entries.empty() && manifest_entries[index].sha256) {
        const std::string expected = NormalizeSha256(
            *manifest_entries[index].sha256, index + 1U);
        const std::string actual = ComputeSha256(paths[index]);
        if (expected != actual) {
          throw ReplayError("SHA-256 mismatch for manifest image: " +
                            paths[index].string());
        }
      }

      common::Frame frame = WicImageCodec::Decode(
          paths[index], source_, static_cast<std::uint64_t>(index));
      if (!manifest_entries.empty()) {
        const auto& entry = manifest_entries[index];
        if (entry.width.has_value() && *entry.width != frame.width) {
          throw ReplayError("Width mismatch for manifest image: " +
                            paths[index].string());
        }
        if (entry.height.has_value() && *entry.height != frame.height) {
          throw ReplayError("Height mismatch for manifest image: " +
                            paths[index].string());
        }
      }
      frames_.push_back(std::move(frame));
    }
  } catch (const ReplayError&) {
    throw;
  } catch (const std::exception& error) {
    throw ReplayError("Rejected replay input '" + input_.string() +
                      "': " + error.what());
  }
}

common::FrameSource ImageReplaySource::Source() const { return source_; }

std::optional<common::Frame> ImageReplaySource::TryGetNextFrame() {
  if (paused_) {
    return std::nullopt;
  }
  return Next();
}

std::optional<common::Frame> ImageReplaySource::Next() {
  if (cursor_ >= frames_.size()) {
    if (!loop_ || frames_.empty()) {
      return std::nullopt;
    }
    cursor_ = 0U;
  }

  const auto capture_started = common::MonotonicTimestamp::clock::now();
  common::Frame frame = frames_[cursor_];
  ++cursor_;
  frame.timestamps.capture_started = capture_started;
  frame.timestamps.capture_completed =
      common::MonotonicTimestamp::clock::now();
  frame.timestamps.captured_at_utc = common::UtcTimestamp::clock::now();
  return frame;
}

void ImageReplaySource::SetPaused(const bool paused) noexcept {
  paused_ = paused;
}

bool ImageReplaySource::IsPaused() const noexcept { return paused_; }

void ImageReplaySource::SetLoop(const bool loop) noexcept { loop_ = loop; }

bool ImageReplaySource::IsLooping() const noexcept { return loop_; }

bool ImageReplaySource::SeekIndex(const std::size_t index) noexcept {
  if (index >= frames_.size()) {
    return false;
  }
  cursor_ = index;
  return true;
}

std::size_t ImageReplaySource::CurrentIndex() const noexcept { return cursor_; }

std::size_t ImageReplaySource::FrameCount() const noexcept {
  return frames_.size();
}

}  // namespace lol_assistant::replay
