#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

namespace lol_assistant::live_client {

inline constexpr std::wstring_view kLiveClientHost = L"127.0.0.1";
inline constexpr std::uint16_t kLiveClientPort = 2999U;

enum class LiveClientEndpoint {
  ActivePlayerName,
  PlayerList,
  ActivePlayer,
};

enum class LiveClientStatus {
  Ready,
  Unavailable,
  InvalidResponse,
};

struct LiveClientPlayerState final {
  std::string champion_name;
  std::uint32_t level{0U};
  bool is_dead{false};
  double respawn_timer_seconds{0.0};
  std::optional<double> current_health{};
  std::optional<double> max_health{};
  std::optional<double> health_percent{};
};

struct LiveClientSnapshot final {
  LiveClientStatus status{LiveClientStatus::Unavailable};
  std::optional<LiveClientPlayerState> player;
  std::string reason;
  std::chrono::system_clock::time_point observed_at{};
};

struct LiveClientEvent final {
  std::uint64_t sequence{0U};
  LiveClientSnapshot snapshot;
};

struct LiveClientHttpResult final {
  bool ok{false};
  std::uint32_t status_code{0U};
  std::string body;
  std::string error_code;
};

class ILiveClientTransport {
public:
  virtual ~ILiveClientTransport() = default;
  [[nodiscard]] virtual LiveClientHttpResult
  Get(LiveClientEndpoint endpoint) = 0;
};

// The endpoint is deliberately fixed to HTTPS loopback:2999. Certificate
// exceptions are therefore never applied to a caller-controlled remote host.
class WinHttpLiveClientTransport final : public ILiveClientTransport {
public:
  WinHttpLiveClientTransport() noexcept = default;
  ~WinHttpLiveClientTransport() override;
  WinHttpLiveClientTransport(const WinHttpLiveClientTransport &) = delete;
  WinHttpLiveClientTransport &
  operator=(const WinHttpLiveClientTransport &) = delete;

  [[nodiscard]] LiveClientHttpResult Get(LiveClientEndpoint endpoint) override;

private:
  [[nodiscard]] bool EnsureConnected(std::string &error_code) noexcept;
  void ResetConnection() noexcept;

  void *session_{nullptr};
  void *connection_{nullptr};
};

[[nodiscard]] std::optional<std::string>
ParseActivePlayerName(std::string_view json, std::string &reason);

[[nodiscard]] std::optional<LiveClientPlayerState>
ParsePlayerListForActivePlayer(std::string_view json,
                               std::string_view active_player_name,
                               std::string &reason);

struct LiveClientVitals final {
  double current_health{0.0};
  double max_health{0.0};
  double health_percent{0.0};
};

[[nodiscard]] std::optional<LiveClientVitals>
ParseActivePlayerVitals(std::string_view json, std::string &reason);

class LiveClientReader final {
public:
  explicit LiveClientReader(std::unique_ptr<ILiveClientTransport> transport);
  LiveClientReader(const LiveClientReader &) = delete;
  LiveClientReader &operator=(const LiveClientReader &) = delete;

  [[nodiscard]] LiveClientSnapshot Read();

private:
  std::unique_ptr<ILiveClientTransport> transport_;
};

class LiveClientEmissionGate final {
public:
  explicit LiveClientEmissionGate(
      std::chrono::milliseconds unchanged_heartbeat);

  [[nodiscard]] bool ShouldEmit(const LiveClientSnapshot &snapshot,
                                std::chrono::steady_clock::time_point now);

private:
  std::chrono::milliseconds unchanged_heartbeat_;
  std::optional<LiveClientSnapshot> previous_;
  std::optional<std::chrono::steady_clock::time_point> last_emit_;
};

struct LiveClientPollerConfig final {
  std::chrono::milliseconds poll_interval{500};
  std::chrono::milliseconds unchanged_heartbeat{30'000};
};

class LiveClientPoller final {
public:
  using Callback = std::function<void(const LiveClientEvent &)>;

  LiveClientPoller(std::unique_ptr<LiveClientReader> reader,
                   LiveClientPollerConfig config, Callback callback);
  ~LiveClientPoller();
  LiveClientPoller(const LiveClientPoller &) = delete;
  LiveClientPoller &operator=(const LiveClientPoller &) = delete;

  void Start();
  void Stop() noexcept;
  [[nodiscard]] bool running() const noexcept;

private:
  void Run() noexcept;

  std::unique_ptr<LiveClientReader> reader_;
  LiveClientPollerConfig config_;
  Callback callback_;
  std::atomic<bool> stop_requested_{false};
  std::atomic<bool> running_{false};
  std::mutex wait_mutex_;
  std::condition_variable wait_condition_;
  std::thread thread_;
};

[[nodiscard]] const char *ToString(LiveClientStatus status) noexcept;

// Returns exactly one UTF-8 JSON object and never appends a newline.
[[nodiscard]] std::string
SerializeLiveClientEventJson(const LiveClientEvent &event,
                             std::string_view session_id);

} // namespace lol_assistant::live_client
