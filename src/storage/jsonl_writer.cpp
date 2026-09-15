#include "lol_assistant/storage/jsonl_writer.h"

#include <array>
#include <charconv>
#include <cmath>
#include <fstream>
#include <limits>
#include <mutex>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

#include "storage_internal.h"

namespace lol_assistant::storage {
namespace {

void AppendEscapedString(const std::string_view value, std::string& output) {
  constexpr char hex[] = "0123456789abcdef";
  output.push_back('"');
  for (const unsigned char character : value) {
    switch (character) {
      case '"':
        output.append("\\\"");
        break;
      case '\\':
        output.append("\\\\");
        break;
      case '\b':
        output.append("\\b");
        break;
      case '\f':
        output.append("\\f");
        break;
      case '\n':
        output.append("\\n");
        break;
      case '\r':
        output.append("\\r");
        break;
      case '\t':
        output.append("\\t");
        break;
      default:
        if (character < 0x20U) {
          output.append("\\u00");
          output.push_back(hex[(character >> 4U) & 0x0FU]);
          output.push_back(hex[character & 0x0FU]);
        } else {
          output.push_back(static_cast<char>(character));
        }
        break;
    }
  }
  output.push_back('"');
}

[[nodiscard]] StorageStatus ValidateEvent(const JsonlEvent& event) {
  if (event.event_type.empty() || event.timestamp_utc.empty() ||
      !internal::IsValidUtf8(event.event_type) ||
      !internal::IsValidUtf8(event.timestamp_utc) ||
      (event.session_id.has_value() &&
       !internal::IsValidUtf8(*event.session_id))) {
    return {StorageErrorCode::InvalidUtf8, 0,
            "JSONL event metadata is empty or is not valid UTF-8"};
  }
  for (const auto& [key, value] : event.fields) {
    if (key.empty() || !internal::IsValidUtf8(key)) {
      return {StorageErrorCode::InvalidUtf8, 0,
              "JSONL field name is empty or is not valid UTF-8"};
    }
    if (const auto* text = std::get_if<std::string>(&value);
        text != nullptr && !internal::IsValidUtf8(*text)) {
      return {StorageErrorCode::InvalidUtf8, 0,
              "JSONL string field is not valid UTF-8"};
    }
    if (const auto* number = std::get_if<double>(&value);
        number != nullptr && !std::isfinite(*number)) {
      return {StorageErrorCode::InvalidArgument, 0,
              "JSONL numeric fields must be finite"};
    }
  }
  return StorageStatus::Ok();
}

void AppendScalar(const JsonScalar& scalar, std::string& output) {
  std::visit(
      [&output](const auto& value) {
        using Value = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<Value, std::nullptr_t>) {
          output.append("null");
        } else if constexpr (std::is_same_v<Value, bool>) {
          output.append(value ? "true" : "false");
        } else if constexpr (std::is_same_v<Value, std::string>) {
          AppendEscapedString(value, output);
        } else {
          std::array<char, 64U> buffer{};
          std::to_chars_result conversion;
          if constexpr (std::is_same_v<Value, double>) {
            conversion =
                std::to_chars(buffer.data(), buffer.data() + buffer.size(),
                              value, std::chars_format::general,
                              std::numeric_limits<double>::max_digits10);
          } else {
            conversion = std::to_chars(buffer.data(),
                                       buffer.data() + buffer.size(), value);
          }
          output.append(buffer.data(), conversion.ptr);
        }
      },
      scalar);
}

[[nodiscard]] std::string Serialize(const JsonlEvent& event) {
  std::string output;
  output.reserve(192U);
  output.append("{\"event_type\":");
  AppendEscapedString(event.event_type, output);
  output.append(",\"timestamp_utc\":");
  AppendEscapedString(event.timestamp_utc, output);
  output.append(",\"session_id\":");
  if (event.session_id.has_value()) {
    AppendEscapedString(*event.session_id, output);
  } else {
    output.append("null");
  }
  output.append(",\"fields\":{");
  bool first = true;
  for (const auto& [key, value] : event.fields) {
    if (!first) {
      output.push_back(',');
    }
    first = false;
    AppendEscapedString(key, output);
    output.push_back(':');
    AppendScalar(value, output);
  }
  output.append("}}\n");
  return output;
}

}  // namespace

struct JsonlWriter::Impl final {
  std::filesystem::path path{};
  std::ofstream stream{};
  mutable std::mutex mutex{};

  ~Impl() {
    if (stream.is_open()) {
      stream.flush();
      stream.close();
    }
  }
};

JsonlWriter::JsonlWriter(std::unique_ptr<Impl> implementation) noexcept
    : impl_(std::move(implementation)) {}

JsonlWriter::~JsonlWriter() {
  if (impl_ != nullptr) {
    (void)Close();
  }
}

JsonlWriter::JsonlWriter(JsonlWriter&&) noexcept = default;
JsonlWriter& JsonlWriter::operator=(JsonlWriter&&) noexcept = default;

StorageStatus JsonlWriter::Open(const std::filesystem::path& jsonl_path,
                                const std::filesystem::path& workspace_root,
                                std::unique_ptr<JsonlWriter>& writer) {
  writer.reset();
  internal::WorkspacePath resolved;
  auto status =
      internal::ResolveWorkspacePath(jsonl_path, workspace_root, resolved);
  if (!status.IsSuccess()) {
    return status;
  }

  auto implementation = std::make_unique<Impl>();
  implementation->path = resolved.target;
  implementation->stream.open(resolved.target,
                              std::ios::binary | std::ios::out | std::ios::app);
  if (!implementation->stream.is_open() || !implementation->stream.good()) {
    return {StorageErrorCode::OpenFailed, 0, "failed to open JSONL output"};
  }
  writer.reset(new JsonlWriter(std::move(implementation)));
  return StorageStatus::Ok();
}

StorageStatus JsonlWriter::Write(const JsonlEvent& event) {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "JSONL writer is closed"};
  }
  const auto validation = ValidateEvent(event);
  if (!validation.IsSuccess()) {
    return validation;
  }
  const auto line = Serialize(event);

  std::scoped_lock lock{impl_->mutex};
  if (!impl_->stream.is_open()) {
    return {StorageErrorCode::Closed, 0, "JSONL writer is closed"};
  }
  impl_->stream.write(line.data(), static_cast<std::streamsize>(line.size()));
  if (!impl_->stream.good()) {
    return {StorageErrorCode::IoError, 0, "failed to append JSONL event"};
  }
  return StorageStatus::Ok();
}

StorageStatus JsonlWriter::Flush() {
  if (impl_ == nullptr) {
    return {StorageErrorCode::Closed, 0, "JSONL writer is closed"};
  }
  std::scoped_lock lock{impl_->mutex};
  if (!impl_->stream.is_open()) {
    return {StorageErrorCode::Closed, 0, "JSONL writer is closed"};
  }
  impl_->stream.flush();
  if (!impl_->stream.good()) {
    return {StorageErrorCode::IoError, 0, "failed to flush JSONL output"};
  }
  return StorageStatus::Ok();
}

StorageStatus JsonlWriter::Close() {
  if (impl_ == nullptr) {
    return StorageStatus::Ok();
  }
  std::scoped_lock lock{impl_->mutex};
  if (!impl_->stream.is_open()) {
    return StorageStatus::Ok();
  }
  impl_->stream.flush();
  if (!impl_->stream.good()) {
    return {StorageErrorCode::IoError, 0,
            "failed to flush JSONL output before close"};
  }
  impl_->stream.close();
  if (impl_->stream.fail()) {
    return {StorageErrorCode::IoError, 0, "failed to close JSONL output"};
  }
  return StorageStatus::Ok();
}

bool JsonlWriter::IsOpen() const noexcept {
  return impl_ != nullptr && impl_->stream.is_open();
}

const std::filesystem::path& JsonlWriter::Path() const noexcept {
  static const std::filesystem::path empty_path;
  return impl_ != nullptr ? impl_->path : empty_path;
}

}  // namespace lol_assistant::storage
