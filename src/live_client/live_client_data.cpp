#include "lol_assistant/live_client/live_client_data.h"

#include <windows.h>
#include <winhttp.h>

#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/base.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace lol_assistant::live_client {
namespace {

using winrt::Windows::Data::Json::JsonArray;
using winrt::Windows::Data::Json::JsonObject;
using winrt::Windows::Data::Json::JsonValue;
using winrt::Windows::Data::Json::JsonValueType;

constexpr std::size_t kMaximumResponseBytes = 1U * 1024U * 1024U;
constexpr int kResolveTimeoutMilliseconds = 250;
constexpr int kConnectTimeoutMilliseconds = 400;
constexpr int kSendTimeoutMilliseconds = 400;
constexpr int kReceiveTimeoutMilliseconds = 500;

[[nodiscard]] std::wstring_view
EndpointPath(const LiveClientEndpoint endpoint) {
  switch (endpoint) {
  case LiveClientEndpoint::ActivePlayerName:
    return L"/liveclientdata/activeplayername";
  case LiveClientEndpoint::PlayerList:
    return L"/liveclientdata/playerlist";
  case LiveClientEndpoint::ActivePlayer:
    return L"/liveclientdata/activeplayer";
  default:
    throw std::invalid_argument("Unknown Live Client endpoint");
  }
}

[[nodiscard]] HINTERNET AsInternetHandle(void *const value) noexcept {
  return static_cast<HINTERNET>(value);
}

[[nodiscard]] std::string LastErrorCode(const char *const prefix) {
  std::string result{prefix};
  result.push_back('_');
  result.append(std::to_string(GetLastError()));
  return result;
}

class ScopedInternetHandle final {
public:
  explicit ScopedInternetHandle(HINTERNET handle = nullptr) noexcept
      : handle_(handle) {}
  ~ScopedInternetHandle() {
    if (handle_ != nullptr) {
      WinHttpCloseHandle(handle_);
    }
  }
  ScopedInternetHandle(const ScopedInternetHandle &) = delete;
  ScopedInternetHandle &operator=(const ScopedInternetHandle &) = delete;
  [[nodiscard]] HINTERNET get() const noexcept { return handle_; }

private:
  HINTERNET handle_{nullptr};
};

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

[[nodiscard]] std::optional<std::string>
NamedString(const JsonObject &object, const wchar_t *const name) {
  if (!object.HasKey(name)) {
    return std::nullopt;
  }
  const auto value = object.GetNamedValue(name);
  if (value.ValueType() != JsonValueType::String) {
    return std::nullopt;
  }
  return winrt::to_string(value.GetString());
}

[[nodiscard]] bool IdentityMatches(const JsonObject &object,
                                   const std::string_view active_name) {
  if (active_name.empty()) {
    return false;
  }

  std::vector<std::string> candidates;
  for (const wchar_t *const field :
       {L"summonerName", L"riotId", L"riotIdGameName"}) {
    if (auto candidate = NamedString(object, field); candidate.has_value()) {
      candidates.push_back(std::move(*candidate));
    }
  }
  const auto game_name = NamedString(object, L"riotIdGameName");
  const auto tag_line = NamedString(object, L"riotIdTagLine");
  if (game_name.has_value() && tag_line.has_value() && !game_name->empty() &&
      !tag_line->empty()) {
    candidates.push_back(*game_name + "#" + *tag_line);
  }
  return std::any_of(candidates.begin(), candidates.end(),
                     [active_name](const std::string &candidate) {
                       return candidate == active_name;
                     });
}

[[nodiscard]] std::optional<double> NamedNumber(const JsonObject &object,
                                                const wchar_t *const name) {
  if (!object.HasKey(name)) {
    return std::nullopt;
  }
  const auto value = object.GetNamedValue(name);
  if (value.ValueType() != JsonValueType::Number) {
    return std::nullopt;
  }
  return value.GetNumber();
}

[[nodiscard]] std::optional<bool> NamedBoolean(const JsonObject &object,
                                               const wchar_t *const name) {
  if (!object.HasKey(name)) {
    return std::nullopt;
  }
  const auto value = object.GetNamedValue(name);
  if (value.ValueType() != JsonValueType::Boolean) {
    return std::nullopt;
  }
  return value.GetBoolean();
}

[[nodiscard]] bool PlayerStatesEqual(const LiveClientPlayerState &left,
                                     const LiveClientPlayerState &right) {
  return left.champion_name == right.champion_name &&
         left.level == right.level && left.is_dead == right.is_dead &&
         left.respawn_timer_seconds == right.respawn_timer_seconds &&
         left.current_health == right.current_health &&
         left.max_health == right.max_health &&
         left.health_percent == right.health_percent;
}

[[nodiscard]] bool SnapshotsEquivalent(const LiveClientSnapshot &left,
                                       const LiveClientSnapshot &right) {
  if (left.status != right.status || left.reason != right.reason ||
      left.player.has_value() != right.player.has_value()) {
    return false;
  }
  return !left.player.has_value() ||
         PlayerStatesEqual(*left.player, *right.player);
}

void AppendEscaped(std::string &output, const std::string_view value) {
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

void AppendDouble(std::string &output, const double value) {
  std::array<char, 64U> buffer{};
  const auto converted = std::to_chars(
      buffer.data(), buffer.data() + buffer.size(), value,
      std::chars_format::general, std::numeric_limits<double>::max_digits10);
  if (converted.ec != std::errc{}) {
    throw std::runtime_error("Unable to format Live Client number");
  }
  output.append(buffer.data(), converted.ptr);
}

[[nodiscard]] std::string
FormatUtc(const std::chrono::system_clock::time_point time) {
  using namespace std::chrono;
  const auto epoch = time.time_since_epoch();
  const auto seconds_part = floor<seconds>(epoch);
  const auto microseconds_part =
      duration_cast<microseconds>(epoch - seconds_part);
  const std::time_t raw_time = static_cast<std::time_t>(seconds_part.count());
  std::tm utc{};
  if (gmtime_s(&utc, &raw_time) != 0) {
    throw std::runtime_error("Unable to format Live Client timestamp");
  }
  std::array<char, 40U> buffer{};
  const int written = std::snprintf(
      buffer.data(), buffer.size(), "%04d-%02d-%02dT%02d:%02d:%02d.%06lldZ",
      utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min,
      utc.tm_sec, static_cast<long long>(microseconds_part.count()));
  if (written <= 0 || static_cast<std::size_t>(written) >= buffer.size()) {
    throw std::runtime_error("Unable to format Live Client timestamp");
  }
  return std::string(buffer.data(), static_cast<std::size_t>(written));
}

[[nodiscard]] LiveClientSnapshot Unavailable(std::string reason) {
  LiveClientSnapshot snapshot;
  snapshot.status = LiveClientStatus::Unavailable;
  snapshot.reason = std::move(reason);
  snapshot.observed_at = std::chrono::system_clock::now();
  return snapshot;
}

[[nodiscard]] LiveClientSnapshot InvalidResponse(std::string reason) {
  LiveClientSnapshot snapshot;
  snapshot.status = LiveClientStatus::InvalidResponse;
  snapshot.reason = std::move(reason);
  snapshot.observed_at = std::chrono::system_clock::now();
  return snapshot;
}

} // namespace

WinHttpLiveClientTransport::~WinHttpLiveClientTransport() { ResetConnection(); }

bool WinHttpLiveClientTransport::EnsureConnected(
    std::string &error_code) noexcept {
  if (session_ == nullptr) {
    session_ = WinHttpOpen(L"lol-augment-assistant/live-client-data",
                           WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME,
                           WINHTTP_NO_PROXY_BYPASS, 0U);
    if (session_ == nullptr) {
      error_code = LastErrorCode("open_failed");
      return false;
    }
    if (WinHttpSetTimeouts(
            AsInternetHandle(session_), kResolveTimeoutMilliseconds,
            kConnectTimeoutMilliseconds, kSendTimeoutMilliseconds,
            kReceiveTimeoutMilliseconds) == FALSE) {
      error_code = LastErrorCode("timeouts_failed");
      ResetConnection();
      return false;
    }
  }
  if (connection_ == nullptr) {
    connection_ = WinHttpConnect(AsInternetHandle(session_),
                                 std::wstring{kLiveClientHost}.c_str(),
                                 kLiveClientPort, 0U);
    if (connection_ == nullptr) {
      error_code = LastErrorCode("connect_failed");
      ResetConnection();
      return false;
    }
  }
  return true;
}

void WinHttpLiveClientTransport::ResetConnection() noexcept {
  if (connection_ != nullptr) {
    WinHttpCloseHandle(AsInternetHandle(connection_));
    connection_ = nullptr;
  }
  if (session_ != nullptr) {
    WinHttpCloseHandle(AsInternetHandle(session_));
    session_ = nullptr;
  }
}

LiveClientHttpResult
WinHttpLiveClientTransport::Get(const LiveClientEndpoint endpoint) {
  LiveClientHttpResult result;
  std::wstring_view path;
  try {
    path = EndpointPath(endpoint);
  } catch (const std::invalid_argument &) {
    result.error_code = "endpoint_not_allowed";
    return result;
  }
  if (!EnsureConnected(result.error_code)) {
    return result;
  }

  const std::wstring request_path{path};
  ScopedInternetHandle request{WinHttpOpenRequest(
      AsInternetHandle(connection_), L"GET", request_path.c_str(), nullptr,
      WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE)};
  if (request.get() == nullptr) {
    result.error_code = LastErrorCode("request_failed");
    ResetConnection();
    return result;
  }

  // Riot's loopback service uses a self-signed certificate. These exceptions
  // are safe here because neither host nor port is caller-configurable.
  DWORD security_flags =
      SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID;
  if (WinHttpSetOption(request.get(), WINHTTP_OPTION_SECURITY_FLAGS,
                       &security_flags, sizeof(security_flags)) == FALSE) {
    result.error_code = LastErrorCode("security_flags_failed");
    return result;
  }
  DWORD redirect_policy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
  if (WinHttpSetOption(request.get(), WINHTTP_OPTION_REDIRECT_POLICY,
                       &redirect_policy, sizeof(redirect_policy)) == FALSE) {
    result.error_code = LastErrorCode("redirect_policy_failed");
    return result;
  }

  constexpr wchar_t headers[] = L"Accept: application/json\r\n";
  if (WinHttpSendRequest(request.get(), headers, static_cast<DWORD>(-1L),
                         WINHTTP_NO_REQUEST_DATA, 0U, 0U, 0U) == FALSE ||
      WinHttpReceiveResponse(request.get(), nullptr) == FALSE) {
    result.error_code = LastErrorCode("exchange_failed");
    ResetConnection();
    return result;
  }

  DWORD status_code = 0U;
  DWORD status_size = sizeof(status_code);
  if (WinHttpQueryHeaders(request.get(),
                          WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                          WINHTTP_HEADER_NAME_BY_INDEX, &status_code,
                          &status_size, WINHTTP_NO_HEADER_INDEX) == FALSE) {
    result.error_code = LastErrorCode("status_failed");
    return result;
  }
  result.status_code = status_code;
  if (status_code != 200U) {
    result.error_code = "http_" + std::to_string(status_code);
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
      result.error_code = LastErrorCode("available_failed");
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
      result.error_code = LastErrorCode("read_failed");
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

std::optional<std::string> ParseActivePlayerName(const std::string_view json,
                                                 std::string &reason) {
  reason.clear();
  try {
    const auto value = JsonValue::Parse(winrt::to_hstring(std::string{json}));
    if (value.ValueType() != JsonValueType::String) {
      reason = "active_player_name_not_string";
      return std::nullopt;
    }
    std::string name = winrt::to_string(value.GetString());
    if (!HasBoundedVisibleText(name, 256U)) {
      reason = "active_player_name_invalid";
      return std::nullopt;
    }
    return name;
  } catch (const winrt::hresult_error &) {
    reason = "active_player_name_invalid_json";
    return std::nullopt;
  }
}

std::optional<LiveClientPlayerState>
ParsePlayerListForActivePlayer(const std::string_view json,
                               const std::string_view active_player_name,
                               std::string &reason) {
  reason.clear();
  try {
    const auto array = JsonArray::Parse(winrt::to_hstring(std::string{json}));
    std::optional<JsonObject> matched;
    for (const auto &value : array) {
      if (value.ValueType() != JsonValueType::Object) {
        reason = "player_list_entry_not_object";
        return std::nullopt;
      }
      const auto object = value.GetObjectW();
      if (!IdentityMatches(object, active_player_name)) {
        continue;
      }
      if (matched.has_value()) {
        reason = "active_player_ambiguous";
        return std::nullopt;
      }
      matched = object;
    }
    if (!matched.has_value()) {
      reason = "active_player_not_found";
      return std::nullopt;
    }

    const auto champion_name = NamedString(*matched, L"championName");
    const auto level = NamedNumber(*matched, L"level");
    const auto is_dead = NamedBoolean(*matched, L"isDead");
    const auto respawn_timer = NamedNumber(*matched, L"respawnTimer");
    if (!champion_name.has_value() || !level.has_value() ||
        !is_dead.has_value() || !respawn_timer.has_value()) {
      reason = "active_player_fields_missing_or_wrong_type";
      return std::nullopt;
    }

    LiveClientPlayerState player;
    player.champion_name = *champion_name;
    if (!HasBoundedVisibleText(player.champion_name, 128U)) {
      reason = "champion_name_invalid";
      return std::nullopt;
    }
    if (!std::isfinite(*level) || std::trunc(*level) != *level ||
        *level < 1.0 || *level > 255.0) {
      reason = "level_out_of_range";
      return std::nullopt;
    }
    if (!std::isfinite(*respawn_timer) || *respawn_timer < 0.0 ||
        *respawn_timer > 3'600.0) {
      reason = "respawn_timer_out_of_range";
      return std::nullopt;
    }
    player.level = static_cast<std::uint32_t>(*level);
    player.is_dead = *is_dead;
    player.respawn_timer_seconds = *respawn_timer;
    return player;
  } catch (const winrt::hresult_error &) {
    reason = "player_list_invalid_json";
    return std::nullopt;
  }
}

std::optional<LiveClientVitals>
ParseActivePlayerVitals(const std::string_view json, std::string &reason) {
  reason.clear();
  try {
    const auto object = JsonObject::Parse(winrt::to_hstring(std::string{json}));
    if (!object.HasKey(L"championStats") ||
        object.GetNamedValue(L"championStats").ValueType() !=
            JsonValueType::Object) {
      reason = "active_player_champion_stats_missing_or_wrong_type";
      return std::nullopt;
    }
    const auto stats = object.GetNamedObject(L"championStats");
    const auto current_health = NamedNumber(stats, L"currentHealth");
    const auto max_health = NamedNumber(stats, L"maxHealth");
    if (!current_health.has_value() || !max_health.has_value()) {
      reason = "active_player_health_missing_or_wrong_type";
      return std::nullopt;
    }
    constexpr double kMaximumPlausibleHealth = 1'000'000'000.0;
    if (!std::isfinite(*current_health) || !std::isfinite(*max_health) ||
        *current_health < 0.0 || *current_health > kMaximumPlausibleHealth ||
        *max_health <= 0.0 || *max_health > kMaximumPlausibleHealth) {
      reason = "active_player_health_out_of_range";
      return std::nullopt;
    }
    LiveClientVitals vitals;
    vitals.current_health = *current_health;
    vitals.max_health = *max_health;
    vitals.health_percent = 100.0 * *current_health / *max_health;
    return vitals;
  } catch (const winrt::hresult_error &) {
    reason = "active_player_invalid_json";
    return std::nullopt;
  }
}

LiveClientReader::LiveClientReader(
    std::unique_ptr<ILiveClientTransport> transport)
    : transport_(std::move(transport)) {
  if (transport_ == nullptr) {
    throw std::invalid_argument("Live Client transport must not be null");
  }
}

LiveClientSnapshot LiveClientReader::Read() {
  const auto active_response =
      transport_->Get(LiveClientEndpoint::ActivePlayerName);
  if (!active_response.ok) {
    return Unavailable("active_player_name_" + active_response.error_code);
  }
  std::string parse_reason;
  const auto active_name =
      ParseActivePlayerName(active_response.body, parse_reason);
  if (!active_name.has_value()) {
    return InvalidResponse(std::move(parse_reason));
  }

  const auto player_list_response =
      transport_->Get(LiveClientEndpoint::PlayerList);
  if (!player_list_response.ok) {
    return Unavailable("player_list_" + player_list_response.error_code);
  }
  auto player = ParsePlayerListForActivePlayer(player_list_response.body,
                                               *active_name, parse_reason);
  if (!player.has_value()) {
    return InvalidResponse(std::move(parse_reason));
  }

  // Health is enrichment from the allowlisted own-player endpoint. Keep the
  // four-field player state READY when this endpoint is briefly unavailable
  // during game startup; missing health is represented explicitly as null.
  const auto active_player_response =
      transport_->Get(LiveClientEndpoint::ActivePlayer);
  if (active_player_response.ok) {
    const auto vitals =
        ParseActivePlayerVitals(active_player_response.body, parse_reason);
    if (vitals.has_value()) {
      player->current_health = vitals->current_health;
      player->max_health = vitals->max_health;
      player->health_percent = vitals->health_percent;
    }
  }

  LiveClientSnapshot snapshot;
  snapshot.status = LiveClientStatus::Ready;
  snapshot.player = std::move(*player);
  snapshot.reason = "ok";
  snapshot.observed_at = std::chrono::system_clock::now();
  return snapshot;
}

LiveClientEmissionGate::LiveClientEmissionGate(
    const std::chrono::milliseconds unchanged_heartbeat)
    : unchanged_heartbeat_(unchanged_heartbeat) {
  if (unchanged_heartbeat_ <= std::chrono::milliseconds::zero()) {
    throw std::invalid_argument(
        "Live Client unchanged heartbeat must be positive");
  }
}

bool LiveClientEmissionGate::ShouldEmit(
    const LiveClientSnapshot &snapshot,
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

LiveClientPoller::LiveClientPoller(std::unique_ptr<LiveClientReader> reader,
                                   const LiveClientPollerConfig config,
                                   Callback callback)
    : reader_(std::move(reader)), config_(config),
      callback_(std::move(callback)) {
  if (reader_ == nullptr || !callback_) {
    throw std::invalid_argument(
        "Live Client poller requires a reader and callback");
  }
  if (config_.poll_interval <= std::chrono::milliseconds::zero() ||
      config_.unchanged_heartbeat <= std::chrono::milliseconds::zero()) {
    throw std::invalid_argument("Live Client poll intervals must be positive");
  }
}

LiveClientPoller::~LiveClientPoller() { Stop(); }

void LiveClientPoller::Start() {
  if (thread_.joinable() || running_.load()) {
    throw std::logic_error("Live Client poller is already running");
  }
  stop_requested_.store(false);
  thread_ = std::thread([this] { Run(); });
}

void LiveClientPoller::Stop() noexcept {
  stop_requested_.store(true);
  wait_condition_.notify_all();
  if (thread_.joinable()) {
    thread_.join();
  }
}

bool LiveClientPoller::running() const noexcept { return running_.load(); }

void LiveClientPoller::Run() noexcept {
  running_.store(true);
  bool apartment_initialized = false;
  try {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    apartment_initialized = true;
    LiveClientEmissionGate gate{config_.unchanged_heartbeat};
    std::uint64_t sequence = 0U;
    while (!stop_requested_.load()) {
      LiveClientSnapshot snapshot;
      try {
        snapshot = reader_->Read();
      } catch (...) {
        snapshot = InvalidResponse("reader_exception");
      }
      const auto now = std::chrono::steady_clock::now();
      if (gate.ShouldEmit(snapshot, now)) {
        LiveClientEvent event;
        event.sequence = ++sequence;
        event.snapshot = std::move(snapshot);
        try {
          callback_(event);
        } catch (...) {
          // Observability must never terminate capture or the polling thread.
        }
      }

      std::unique_lock lock(wait_mutex_);
      wait_condition_.wait_for(lock, config_.poll_interval,
                               [this] { return stop_requested_.load(); });
    }
  } catch (...) {
    // Startup failures remain isolated from the capture/OCR path.
  }
  if (apartment_initialized) {
    winrt::uninit_apartment();
  }
  running_.store(false);
}

const char *ToString(const LiveClientStatus status) noexcept {
  switch (status) {
  case LiveClientStatus::Ready:
    return "READY";
  case LiveClientStatus::Unavailable:
    return "UNAVAILABLE";
  case LiveClientStatus::InvalidResponse:
    return "INVALID_RESPONSE";
  default:
    return "INVALID_RESPONSE";
  }
}

std::string SerializeLiveClientEventJson(const LiveClientEvent &event,
                                         const std::string_view session_id) {
  std::string output{"{\"type\":\"live_client_state\",\"schema_version\":1,"};
  output.append("\"session_id\":");
  AppendEscaped(output, session_id);
  output.append(",\"sequence\":");
  output.append(std::to_string(event.sequence));
  output.append(",\"observed_at_utc\":");
  AppendEscaped(output, FormatUtc(event.snapshot.observed_at));
  output.append(",\"source\":\"live_client_data_api\",\"status\":");
  AppendEscaped(output, ToString(event.snapshot.status));
  output.append(",\"reason\":");
  AppendEscaped(output, event.snapshot.reason);
  output.append(",\"player\":");
  if (!event.snapshot.player.has_value()) {
    output.append("null");
  } else {
    const auto &player = *event.snapshot.player;
    output.append("{\"championName\":");
    AppendEscaped(output, player.champion_name);
    output.append(",\"level\":");
    output.append(std::to_string(player.level));
    output.append(",\"isDead\":");
    output.append(player.is_dead ? "true" : "false");
    output.append(",\"respawnTimer\":");
    AppendDouble(output, player.respawn_timer_seconds);
    output.append(",\"currentHealth\":");
    if (player.current_health.has_value()) {
      AppendDouble(output, *player.current_health);
    } else {
      output.append("null");
    }
    output.append(",\"maxHealth\":");
    if (player.max_health.has_value()) {
      AppendDouble(output, *player.max_health);
    } else {
      output.append("null");
    }
    output.append(",\"healthPercent\":");
    if (player.health_percent.has_value()) {
      AppendDouble(output, *player.health_percent);
    } else {
      output.append("null");
    }
    output.push_back('}');
  }
  output.push_back('}');
  return output;
}

} // namespace lol_assistant::live_client
