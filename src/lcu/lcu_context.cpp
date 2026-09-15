#include "lol_assistant/lcu/lcu_context.h"

// clang-format off: Winsock must precede Windows headers.
#include <winsock2.h>
#include <windows.h>
#include <iphlpapi.h>
#include <winhttp.h>
// clang-format on

#include <winrt/Windows.Data.Json.h>
#include <winrt/base.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <system_error>
#include <utility>
#include <vector>

namespace lol_assistant::lcu {
namespace {

using winrt::Windows::Data::Json::JsonValue;
using winrt::Windows::Data::Json::JsonValueType;

constexpr std::size_t kMaximumResponseBytes = 1U * 1024U * 1024U;
constexpr std::size_t kMaximumLcuLogBytes = 2U * 1024U * 1024U;
constexpr std::size_t kMaximumLcuLogCandidates = 16U;

class ScopedInternetHandle final {
 public:
  explicit ScopedInternetHandle(HINTERNET handle = nullptr) noexcept
      : handle_(handle) {}
  ~ScopedInternetHandle() {
    if (handle_ != nullptr) {
      WinHttpCloseHandle(handle_);
    }
  }
  ScopedInternetHandle(const ScopedInternetHandle&) = delete;
  ScopedInternetHandle& operator=(const ScopedInternetHandle&) = delete;
  [[nodiscard]] HINTERNET get() const noexcept { return handle_; }

 private:
  HINTERNET handle_{nullptr};
};

[[nodiscard]] std::wstring_view EndpointPath(const LcuEndpoint endpoint) {
  switch (endpoint) {
    case LcuEndpoint::GameflowPhase:
      return L"/lol-gameflow/v1/gameflow-phase";
    case LcuEndpoint::CurrentChampion:
      return L"/lol-champ-select/v1/current-champion";
    default:
      throw std::invalid_argument("Unknown LCU endpoint");
  }
}

[[nodiscard]] std::string LastErrorCode(const char* const prefix) {
  std::string result{prefix};
  result.push_back('_');
  result.append(std::to_string(GetLastError()));
  return result;
}

[[nodiscard]] std::optional<std::string> EnvironmentValue(
    const char* const name) {
  SetLastError(ERROR_SUCCESS);
  const DWORD required = GetEnvironmentVariableA(name, nullptr, 0U);
  if (required == 0U) {
    return std::nullopt;
  }
  std::vector<char> buffer(static_cast<std::size_t>(required), '\0');
  const DWORD written = GetEnvironmentVariableA(name, buffer.data(), required);
  if (written == 0U || written >= required) {
    return std::nullopt;
  }
  return std::string(buffer.data(), static_cast<std::size_t>(written));
}

[[nodiscard]] std::optional<std::wstring> EnvironmentValueWide(
    const wchar_t* const name) {
  SetLastError(ERROR_SUCCESS);
  const DWORD required = GetEnvironmentVariableW(name, nullptr, 0U);
  if (required == 0U) {
    return std::nullopt;
  }
  std::vector<wchar_t> buffer(static_cast<std::size_t>(required), L'\0');
  const DWORD written = GetEnvironmentVariableW(name, buffer.data(), required);
  if (written == 0U || written >= required) {
    return std::nullopt;
  }
  return std::wstring(buffer.data(), static_cast<std::size_t>(written));
}

[[nodiscard]] bool IsSafeToken(const std::string_view token) {
  if (token.empty() || token.size() > 1024U) {
    return false;
  }
  return std::all_of(token.begin(), token.end(), [](const char character) {
    const auto byte = static_cast<unsigned char>(character);
    return byte >= 0x21U && byte <= 0x7EU && character != ':';
  });
}

[[nodiscard]] bool HasBoundedVisibleText(const std::string_view value,
                                         const std::size_t maximum_bytes) {
  if (value.empty() || value.size() > maximum_bytes) {
    return false;
  }
  bool visible = false;
  for (const unsigned char character : value) {
    if (character < 0x20U || character == 0x7FU) {
      return false;
    }
    visible = visible || character != static_cast<unsigned char>(' ');
  }
  return visible;
}

[[nodiscard]] bool IsArgumentBoundary(const char value) noexcept {
  const auto byte = static_cast<unsigned char>(value);
  return std::isspace(byte) != 0 || value == '"' || value == '\'' ||
         value == '[' || value == '(';
}

[[nodiscard]] std::vector<std::string> FindArgumentValues(
    const std::string_view text, const std::string_view name) {
  const std::string marker = std::string{"--"} + std::string{name};
  std::vector<std::string> values;
  std::size_t search_from = 0U;
  while (search_from < text.size()) {
    const std::size_t found = text.find(marker, search_from);
    if (found == std::string_view::npos) {
      break;
    }
    search_from = found + marker.size();
    if (found != 0U && !IsArgumentBoundary(text[found - 1U])) {
      continue;
    }

    std::size_t cursor = search_from;
    if (cursor < text.size() && text[cursor] == '=') {
      ++cursor;
    } else if (cursor < text.size() &&
               std::isspace(static_cast<unsigned char>(text[cursor])) != 0) {
      while (cursor < text.size() &&
             std::isspace(static_cast<unsigned char>(text[cursor])) != 0) {
        ++cursor;
      }
    } else {
      continue;
    }
    while (cursor < text.size() &&
           std::isspace(static_cast<unsigned char>(text[cursor])) != 0) {
      ++cursor;
    }
    if (cursor >= text.size()) {
      continue;
    }

    const char quote =
        text[cursor] == '"' || text[cursor] == '\'' ? text[cursor++] : '\0';
    const std::size_t value_begin = cursor;
    if (quote != '\0') {
      while (cursor < text.size() && text[cursor] != quote &&
             text[cursor] != '\r' && text[cursor] != '\n') {
        ++cursor;
      }
      if (cursor >= text.size() || text[cursor] != quote) {
        continue;
      }
    } else {
      while (cursor < text.size() &&
             std::isspace(static_cast<unsigned char>(text[cursor])) == 0 &&
             text[cursor] != ']' && text[cursor] != ')') {
        ++cursor;
      }
    }
    if (cursor > value_begin && cursor - value_begin <= 2048U) {
      values.emplace_back(text.substr(value_begin, cursor - value_begin));
    }
  }
  return values;
}

[[nodiscard]] std::optional<std::string> UniqueArgumentValue(
    const std::string_view text, const std::string_view name,
    std::string& reason) {
  const auto values = FindArgumentValues(text, name);
  if (values.empty()) {
    reason = "launch_arguments_missing";
    return std::nullopt;
  }
  if (std::any_of(values.begin() + 1U, values.end(),
                  [&values](const std::string& value) {
                    return value != values.front();
                  })) {
    reason = "launch_arguments_conflict";
    return std::nullopt;
  }
  return values.front();
}

template <typename Integer>
[[nodiscard]] std::optional<Integer> ParsePositiveInteger(
    const std::string_view value) {
  Integer parsed_value{};
  const auto parsed =
      std::from_chars(value.data(), value.data() + value.size(), parsed_value);
  if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() ||
      parsed_value == 0U) {
    return std::nullopt;
  }
  return parsed_value;
}

[[nodiscard]] bool IsActiveLoopbackListener(const std::uint32_t app_pid,
                                            const std::uint16_t port) {
  ULONG bytes = 0U;
  DWORD status = GetExtendedTcpTable(nullptr, &bytes, FALSE, AF_INET,
                                     TCP_TABLE_OWNER_PID_LISTENER, 0U);
  if (status != ERROR_INSUFFICIENT_BUFFER || bytes == 0U) {
    return false;
  }
  std::vector<std::byte> storage(static_cast<std::size_t>(bytes));
  auto* table = reinterpret_cast<PMIB_TCPTABLE_OWNER_PID>(storage.data());
  status = GetExtendedTcpTable(table, &bytes, FALSE, AF_INET,
                               TCP_TABLE_OWNER_PID_LISTENER, 0U);
  if (status != NO_ERROR) {
    return false;
  }
  for (DWORD index = 0U; index < table->dwNumEntries; ++index) {
    const auto& row = table->table[index];
    if (row.dwState == MIB_TCP_STATE_LISTEN && row.dwOwningPid == app_pid &&
        row.dwLocalAddr == htonl(INADDR_LOOPBACK) &&
        ntohs(static_cast<u_short>(row.dwLocalPort)) == port) {
      return true;
    }
  }
  return false;
}

[[nodiscard]] std::optional<std::string> ReadBoundedLogPrefix(
    const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return std::nullopt;
  }
  std::string text(kMaximumLcuLogBytes, '\0');
  input.read(text.data(), static_cast<std::streamsize>(text.size()));
  const auto read = input.gcount();
  if (read <= 0) {
    return std::nullopt;
  }
  text.resize(static_cast<std::size_t>(read));
  return text;
}

[[nodiscard]] std::optional<LcuLaunchArguments> DiscoverFromLeagueRoot(
    const std::filesystem::path& league_root) {
  const auto log_directory = league_root / L"LeagueClient";
  std::error_code error;
  if (!std::filesystem::is_directory(log_directory, error) || error) {
    return std::nullopt;
  }

  struct CandidateLog final {
    std::filesystem::path path;
    std::filesystem::file_time_type modified;
  };
  std::vector<CandidateLog> logs;
  std::filesystem::directory_iterator iterator(log_directory, error);
  const std::filesystem::directory_iterator end;
  while (!error && iterator != end) {
    const auto& entry = *iterator;
    std::error_code entry_error;
    if (entry.is_regular_file(entry_error) && !entry_error &&
        entry.path().filename().wstring().ends_with(L"_LeagueClientUx.log")) {
      const auto modified = entry.last_write_time(entry_error);
      if (!entry_error) {
        logs.push_back(CandidateLog{entry.path(), modified});
      }
    }
    iterator.increment(error);
  }
  std::sort(logs.begin(), logs.end(),
            [](const CandidateLog& left, const CandidateLog& right) {
              return left.modified > right.modified;
            });
  if (logs.size() > kMaximumLcuLogCandidates) {
    logs.resize(kMaximumLcuLogCandidates);
  }

  for (const auto& log : logs) {
    const auto text = ReadBoundedLogPrefix(log.path);
    if (!text.has_value()) {
      continue;
    }
    std::string reason;
    auto arguments = ParseLcuLaunchArguments(*text, reason);
    if (!arguments.has_value() ||
        !IsActiveLoopbackListener(arguments->app_pid, arguments->port)) {
      continue;
    }
    return arguments;
  }
  return std::nullopt;
}

[[nodiscard]] std::string Base64Encode(const std::string_view input) {
  constexpr char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string output;
  output.reserve(((input.size() + 2U) / 3U) * 4U);
  for (std::size_t index = 0U; index < input.size(); index += 3U) {
    const std::uint32_t first = static_cast<unsigned char>(input[index]);
    const std::uint32_t second =
        index + 1U < input.size()
            ? static_cast<unsigned char>(input[index + 1U])
            : 0U;
    const std::uint32_t third =
        index + 2U < input.size()
            ? static_cast<unsigned char>(input[index + 2U])
            : 0U;
    const std::uint32_t block = (first << 16U) | (second << 8U) | third;
    output.push_back(alphabet[(block >> 18U) & 0x3FU]);
    output.push_back(alphabet[(block >> 12U) & 0x3FU]);
    output.push_back(index + 1U < input.size() ? alphabet[(block >> 6U) & 0x3FU]
                                               : '=');
    output.push_back(index + 2U < input.size() ? alphabet[block & 0x3FU] : '=');
  }
  return output;
}

[[nodiscard]] std::wstring AsciiToWide(const std::string_view input) {
  std::wstring output;
  output.reserve(input.size());
  for (const unsigned char character : input) {
    if (character > 0x7FU) {
      throw std::invalid_argument("LCU header must be ASCII");
    }
    output.push_back(static_cast<wchar_t>(character));
  }
  return output;
}

[[nodiscard]] bool ContextsEqual(const LcuContext& left,
                                 const LcuContext& right) {
  return left.gameflow_phase == right.gameflow_phase &&
         left.champion_id == right.champion_id;
}

[[nodiscard]] bool SnapshotsEquivalent(const LcuContextSnapshot& left,
                                       const LcuContextSnapshot& right) {
  if (left.status != right.status || left.reason != right.reason ||
      left.context.has_value() != right.context.has_value()) {
    return false;
  }
  return !left.context.has_value() ||
         ContextsEqual(*left.context, *right.context);
}

[[nodiscard]] LcuContextSnapshot MakeSnapshot(const LcuContextStatus status,
                                              std::string reason) {
  LcuContextSnapshot snapshot;
  snapshot.status = status;
  snapshot.reason = std::move(reason);
  snapshot.observed_at = std::chrono::system_clock::now();
  return snapshot;
}

void AppendEscaped(std::string& output, const std::string_view value) {
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

[[nodiscard]] std::string FormatUtc(
    const std::chrono::system_clock::time_point time) {
  using namespace std::chrono;
  const auto epoch = time.time_since_epoch();
  const auto seconds_part = floor<seconds>(epoch);
  const auto microseconds_part =
      duration_cast<microseconds>(epoch - seconds_part);
  const std::time_t raw_time = static_cast<std::time_t>(seconds_part.count());
  std::tm utc{};
  if (gmtime_s(&utc, &raw_time) != 0) {
    throw std::runtime_error("Unable to format LCU timestamp");
  }
  std::array<char, 40U> buffer{};
  const int written = std::snprintf(
      buffer.data(), buffer.size(), "%04d-%02d-%02dT%02d:%02d:%02d.%06lldZ",
      utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min,
      utc.tm_sec, static_cast<long long>(microseconds_part.count()));
  if (written <= 0 || static_cast<std::size_t>(written) >= buffer.size()) {
    throw std::runtime_error("Unable to format LCU timestamp");
  }
  return std::string(buffer.data(), static_cast<std::size_t>(written));
}

}  // namespace

std::optional<LcuLaunchArguments> ParseLcuLaunchArguments(
    const std::string_view text, std::string& reason) {
  reason.clear();
  const auto app_pid_text = UniqueArgumentValue(text, "app-pid", reason);
  if (!app_pid_text.has_value()) {
    return std::nullopt;
  }
  const auto port_text = UniqueArgumentValue(text, "app-port", reason);
  if (!port_text.has_value()) {
    return std::nullopt;
  }
  const auto token = UniqueArgumentValue(text, "remoting-auth-token", reason);
  if (!token.has_value()) {
    return std::nullopt;
  }
  const auto app_pid = ParsePositiveInteger<std::uint32_t>(*app_pid_text);
  const auto port = ParsePositiveInteger<std::uint16_t>(*port_text);
  if (!app_pid.has_value() || !port.has_value() || !IsSafeToken(*token)) {
    reason = "launch_arguments_invalid";
    return std::nullopt;
  }
  reason = "ok";
  return LcuLaunchArguments{*app_pid, *port, *token};
}

EnvironmentLcuConnectionProvider::~EnvironmentLcuConnectionProvider() {
  Invalidate();
}

void EnvironmentLcuConnectionProvider::Invalidate() noexcept {
  if (cached_log_arguments_.has_value() &&
      !cached_log_arguments_->token.empty()) {
    SecureZeroMemory(cached_log_arguments_->token.data(),
                     cached_log_arguments_->token.size());
  }
  cached_log_arguments_.reset();
  cached_league_root_.clear();
}

LcuConnectionResult EnvironmentLcuConnectionProvider::Resolve() {
  LcuConnectionResult result;
  const auto port_text = EnvironmentValue("LOL_ASSISTANT_LCU_PORT");
  const auto token = EnvironmentValue("LOL_ASSISTANT_LCU_TOKEN");
  if (port_text.has_value() || token.has_value()) {
    Invalidate();
    if (!port_text.has_value() || !token.has_value() || !IsSafeToken(*token)) {
      result.reason = "auth_unavailable";
      return result;
    }
    const auto port = ParsePositiveInteger<std::uint16_t>(*port_text);
    if (!port.has_value()) {
      result.reason = "auth_unavailable";
      return result;
    }
    result.connection = LcuConnection{*port, *token};
    result.reason = "ok";
    return result;
  }

  const auto league_root = EnvironmentValueWide(L"LOL_ASSISTANT_LEAGUE_ROOT");
  if (!league_root.has_value() || league_root->empty()) {
    Invalidate();
    result.reason = "auth_unavailable";
    return result;
  }
  const std::filesystem::path root{*league_root};
  if (!root.is_absolute()) {
    Invalidate();
    result.reason = "auth_unavailable";
    return result;
  }
  const auto normalized_root = root.lexically_normal();
  if (cached_log_arguments_.has_value() &&
      cached_league_root_ == normalized_root.wstring() &&
      IsActiveLoopbackListener(cached_log_arguments_->app_pid,
                               cached_log_arguments_->port)) {
    result.connection = LcuConnection{cached_log_arguments_->port,
                                      cached_log_arguments_->token};
    result.reason = "ok";
    return result;
  }

  Invalidate();
  cached_log_arguments_ = DiscoverFromLeagueRoot(normalized_root);
  if (!cached_log_arguments_.has_value()) {
    result.reason = "auth_unavailable";
    return result;
  }
  cached_league_root_ = normalized_root.wstring();
  result.connection =
      LcuConnection{cached_log_arguments_->port, cached_log_arguments_->token};
  result.reason = "ok";
  return result;
}

LcuHttpResult WinHttpLcuContextTransport::Get(const LcuConnection& connection,
                                              const LcuEndpoint endpoint) {
  LcuHttpResult result;
  if (connection.port == 0U || !IsSafeToken(connection.token)) {
    result.error_code = "auth_unavailable";
    return result;
  }
  std::wstring_view endpoint_path;
  try {
    endpoint_path = EndpointPath(endpoint);
  } catch (const std::invalid_argument&) {
    result.error_code = "endpoint_not_allowed";
    return result;
  }

  ScopedInternetHandle session{WinHttpOpen(
      L"lol-augment-assistant/lcu-context", WINHTTP_ACCESS_TYPE_NO_PROXY,
      WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0U)};
  if (session.get() == nullptr) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }
  if (WinHttpSetTimeouts(session.get(), 250, 400, 400, 500) == FALSE) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }
  ScopedInternetHandle connection_handle{
      WinHttpConnect(session.get(), L"127.0.0.1", connection.port, 0U)};
  if (connection_handle.get() == nullptr) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }
  const std::wstring path{endpoint_path};
  ScopedInternetHandle request{WinHttpOpenRequest(
      connection_handle.get(), L"GET", path.c_str(), nullptr,
      WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE)};
  if (request.get() == nullptr) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }

  DWORD security_flags =
      SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID;
  if (WinHttpSetOption(request.get(), WINHTTP_OPTION_SECURITY_FLAGS,
                       &security_flags, sizeof(security_flags)) == FALSE) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }
  DWORD redirect_policy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
  if (WinHttpSetOption(request.get(), WINHTTP_OPTION_REDIRECT_POLICY,
                       &redirect_policy, sizeof(redirect_policy)) == FALSE) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }

  const std::string encoded =
      Base64Encode(std::string{"riot:"} + connection.token);
  const std::wstring headers = L"Authorization: Basic " + AsciiToWide(encoded) +
                               L"\r\nAccept: application/json\r\n";
  if (WinHttpSendRequest(request.get(), headers.c_str(),
                         static_cast<DWORD>(-1L), WINHTTP_NO_REQUEST_DATA, 0U,
                         0U, 0U) == FALSE ||
      WinHttpReceiveResponse(request.get(), nullptr) == FALSE) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }

  DWORD status_code = 0U;
  DWORD status_size = sizeof(status_code);
  if (WinHttpQueryHeaders(request.get(),
                          WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                          WINHTTP_HEADER_NAME_BY_INDEX, &status_code,
                          &status_size, WINHTTP_NO_HEADER_INDEX) == FALSE) {
    result.error_code = LastErrorCode("connect_failed");
    return result;
  }
  result.status_code = status_code;
  if (status_code != 200U) {
    result.error_code = status_code == 401U || status_code == 403U
                            ? "auth_rejected"
                            : "http_error";
    return result;
  }

  DWORD content_length = 0U;
  DWORD content_length_size = sizeof(content_length);
  if (WinHttpQueryHeaders(
          request.get(),
          WINHTTP_QUERY_CONTENT_LENGTH | WINHTTP_QUERY_FLAG_NUMBER,
          WINHTTP_HEADER_NAME_BY_INDEX, &content_length, &content_length_size,
          WINHTTP_NO_HEADER_INDEX) != FALSE &&
      static_cast<std::size_t>(content_length) > kMaximumResponseBytes) {
    result.error_code = "response_too_large";
    return result;
  }

  while (true) {
    DWORD available = 0U;
    if (WinHttpQueryDataAvailable(request.get(), &available) == FALSE) {
      result.error_code = LastErrorCode("connect_failed");
      result.body.clear();
      return result;
    }
    if (available == 0U) {
      break;
    }
    if (available > kMaximumResponseBytes - result.body.size()) {
      result.error_code = "response_too_large";
      result.body.clear();
      return result;
    }
    std::string chunk(static_cast<std::size_t>(available), '\0');
    DWORD read = 0U;
    if (WinHttpReadData(request.get(), chunk.data(), available, &read) ==
        FALSE) {
      result.error_code = LastErrorCode("connect_failed");
      result.body.clear();
      return result;
    }
    if (read == 0U) {
      break;
    }
    chunk.resize(static_cast<std::size_t>(read));
    result.body.append(chunk);
  }
  result.ok = true;
  return result;
}

std::optional<std::string> ParseGameflowPhase(const std::string_view json,
                                              std::string& reason) {
  reason.clear();
  try {
    const auto value = JsonValue::Parse(winrt::to_hstring(std::string{json}));
    if (value.ValueType() != JsonValueType::String) {
      reason = "gameflow_invalid_response";
      return std::nullopt;
    }
    std::string phase = winrt::to_string(value.GetString());
    if (!HasBoundedVisibleText(phase, 64U)) {
      reason = "gameflow_invalid_response";
      return std::nullopt;
    }
    return phase;
  } catch (const winrt::hresult_error&) {
    reason = "gameflow_invalid_response";
    return std::nullopt;
  }
}

std::optional<std::uint32_t> ParseCurrentChampionId(const std::string_view json,
                                                    std::string& reason) {
  reason.clear();
  try {
    const auto value = JsonValue::Parse(winrt::to_hstring(std::string{json}));
    if (value.ValueType() != JsonValueType::Number) {
      reason = "champion_invalid_response";
      return std::nullopt;
    }
    const double champion_id = value.GetNumber();
    if (!std::isfinite(champion_id) || std::trunc(champion_id) != champion_id ||
        champion_id < 0.0 ||
        champion_id >
            static_cast<double>(std::numeric_limits<std::uint32_t>::max())) {
      reason = "champion_invalid_response";
      return std::nullopt;
    }
    if (champion_id == 0.0) {
      reason = "champion_unavailable";
      return std::nullopt;
    }
    return static_cast<std::uint32_t>(champion_id);
  } catch (const winrt::hresult_error&) {
    reason = "champion_invalid_response";
    return std::nullopt;
  }
}

LcuContextReader::LcuContextReader(
    std::unique_ptr<ILcuConnectionProvider> provider,
    std::unique_ptr<ILcuContextTransport> transport)
    : provider_(std::move(provider)), transport_(std::move(transport)) {
  if (provider_ == nullptr || transport_ == nullptr) {
    throw std::invalid_argument("LCU reader dependencies must not be null");
  }
}

LcuContextSnapshot LcuContextReader::Read() {
  const auto resolved = provider_->Resolve();
  if (!resolved.connection.has_value()) {
    return MakeSnapshot(LcuContextStatus::Unavailable, "auth_unavailable");
  }
  const auto phase_response =
      transport_->Get(*resolved.connection, LcuEndpoint::GameflowPhase);
  if (!phase_response.ok) {
    if (phase_response.error_code == "auth_rejected") {
      provider_->Invalidate();
    }
    return MakeSnapshot(LcuContextStatus::Unavailable,
                        phase_response.error_code == "auth_rejected"
                            ? "auth_rejected"
                            : "connect_failed");
  }
  std::string reason;
  const auto phase = ParseGameflowPhase(phase_response.body, reason);
  if (!phase.has_value()) {
    return MakeSnapshot(LcuContextStatus::InvalidResponse,
                        "gameflow_invalid_response");
  }

  LcuContextSnapshot snapshot = MakeSnapshot(LcuContextStatus::Ready, "ok");
  snapshot.context = LcuContext{*phase, std::nullopt};
  if (*phase != "ChampSelect") {
    return snapshot;
  }

  const auto champion_response =
      transport_->Get(*resolved.connection, LcuEndpoint::CurrentChampion);
  if (!champion_response.ok) {
    if (champion_response.error_code == "auth_rejected") {
      provider_->Invalidate();
      return MakeSnapshot(LcuContextStatus::Unavailable, "auth_rejected");
    }
    snapshot.status = LcuContextStatus::Partial;
    snapshot.reason = "champion_unavailable";
    return snapshot;
  }
  const auto champion_id =
      ParseCurrentChampionId(champion_response.body, reason);
  if (!champion_id.has_value()) {
    snapshot.status = LcuContextStatus::Partial;
    snapshot.reason = reason == "champion_unavailable"
                          ? "champion_unavailable"
                          : "champion_invalid_response";
    return snapshot;
  }
  snapshot.context->champion_id = *champion_id;
  return snapshot;
}

LcuContextEmissionGate::LcuContextEmissionGate(
    const std::chrono::milliseconds unchanged_heartbeat)
    : unchanged_heartbeat_(unchanged_heartbeat) {
  if (unchanged_heartbeat_ <= std::chrono::milliseconds::zero()) {
    throw std::invalid_argument("LCU unchanged heartbeat must be positive");
  }
}

bool LcuContextEmissionGate::ShouldEmit(
    const LcuContextSnapshot& snapshot,
    const std::chrono::steady_clock::time_point now) {
  const bool changed =
      !previous_.has_value() || !SnapshotsEquivalent(*previous_, snapshot);
  const bool heartbeat_due =
      last_emit_.has_value() && now - *last_emit_ >= unchanged_heartbeat_;
  previous_ = snapshot;
  if (!changed && !heartbeat_due) {
    return false;
  }
  last_emit_ = now;
  return true;
}

LcuContextPoller::LcuContextPoller(std::unique_ptr<LcuContextReader> reader,
                                   const LcuContextPollerConfig config,
                                   Callback callback)
    : reader_(std::move(reader)),
      config_(config),
      callback_(std::move(callback)) {
  if (reader_ == nullptr || !callback_) {
    throw std::invalid_argument("LCU poller requires a reader and callback");
  }
  if (config_.poll_interval <= std::chrono::milliseconds::zero() ||
      config_.unchanged_heartbeat <= std::chrono::milliseconds::zero()) {
    throw std::invalid_argument("LCU poll intervals must be positive");
  }
}

LcuContextPoller::~LcuContextPoller() { Stop(); }

void LcuContextPoller::Start() {
  if (thread_.joinable() || running_.load()) {
    throw std::logic_error("LCU poller is already running");
  }
  stop_requested_.store(false);
  thread_ = std::thread([this] { Run(); });
}

void LcuContextPoller::Stop() noexcept {
  stop_requested_.store(true);
  wait_condition_.notify_all();
  if (thread_.joinable()) {
    thread_.join();
  }
}

bool LcuContextPoller::running() const noexcept { return running_.load(); }

void LcuContextPoller::Run() noexcept {
  running_.store(true);
  bool apartment_initialized = false;
  try {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    apartment_initialized = true;
    LcuContextEmissionGate gate{config_.unchanged_heartbeat};
    std::uint64_t sequence = 0U;
    while (!stop_requested_.load()) {
      LcuContextSnapshot snapshot;
      try {
        snapshot = reader_->Read();
      } catch (...) {
        snapshot =
            MakeSnapshot(LcuContextStatus::Unavailable, "connect_failed");
      }
      if (gate.ShouldEmit(snapshot, std::chrono::steady_clock::now())) {
        LcuContextEvent event;
        event.sequence = ++sequence;
        event.snapshot = std::move(snapshot);
        try {
          callback_(event);
        } catch (...) {
          // LCU context is optional and must not terminate capture.
        }
      }
      std::unique_lock lock(wait_mutex_);
      wait_condition_.wait_for(lock, config_.poll_interval,
                               [this] { return stop_requested_.load(); });
    }
  } catch (...) {
    // Thread initialization failure remains isolated from OCR/capture.
  }
  if (apartment_initialized) {
    winrt::uninit_apartment();
  }
  running_.store(false);
}

const char* ToString(const LcuContextStatus status) noexcept {
  switch (status) {
    case LcuContextStatus::Ready:
      return "READY";
    case LcuContextStatus::Partial:
      return "PARTIAL";
    case LcuContextStatus::Unavailable:
      return "UNAVAILABLE";
    case LcuContextStatus::InvalidResponse:
      return "INVALID_RESPONSE";
    default:
      return "INVALID_RESPONSE";
  }
}

std::string SerializeLcuContextEventJson(const LcuContextEvent& event,
                                         const std::string_view session_id) {
  std::string output{"{\"type\":\"lcu_context_state\",\"schema_version\":1,"};
  output.append("\"session_id\":");
  AppendEscaped(output, session_id);
  output.append(",\"sequence\":");
  output.append(std::to_string(event.sequence));
  output.append(",\"observed_at_utc\":");
  AppendEscaped(output, FormatUtc(event.snapshot.observed_at));
  output.append(",\"status\":");
  AppendEscaped(output, ToString(event.snapshot.status));
  output.append(",\"reason\":");
  AppendEscaped(output, event.snapshot.reason);
  output.append(",\"context\":");
  if (!event.snapshot.context.has_value()) {
    output.append("null");
  } else {
    output.append("{\"gameflowPhase\":");
    AppendEscaped(output, event.snapshot.context->gameflow_phase);
    output.append(",\"championId\":");
    if (event.snapshot.context->champion_id.has_value()) {
      output.append(std::to_string(*event.snapshot.context->champion_id));
    } else {
      output.append("null");
    }
    output.push_back('}');
  }
  output.push_back('}');
  return output;
}

}  // namespace lol_assistant::lcu
