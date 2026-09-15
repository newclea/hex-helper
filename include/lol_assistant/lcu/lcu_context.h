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

namespace lol_assistant::lcu {

inline constexpr std::string_view kLcuPortEnvironment =
    "LOL_ASSISTANT_LCU_PORT";
inline constexpr std::string_view kLcuTokenEnvironment =
    "LOL_ASSISTANT_LCU_TOKEN";
inline constexpr std::string_view kLeagueRootEnvironment =
    "LOL_ASSISTANT_LEAGUE_ROOT";

enum class LcuContextStatus {
  Ready,
  Partial,
  Unavailable,
  InvalidResponse,
};

enum class LcuEndpoint {
  GameflowPhase,
  CurrentChampion,
};

struct LcuConnection final {
  std::uint16_t port{0U};
  std::string token;
};

struct LcuConnectionResult final {
  std::optional<LcuConnection> connection;
  std::string reason;
};

struct LcuLaunchArguments final {
  std::uint32_t app_pid{0U};
  std::uint16_t port{0U};
  std::string token;
};

// Parses the standard arguments written by LeagueClientUx. Repeated copies
// must agree; conflicting values fail closed.
[[nodiscard]] std::optional<LcuLaunchArguments> ParseLcuLaunchArguments(
    std::string_view text, std::string& reason);

class ILcuConnectionProvider {
 public:
  virtual ~ILcuConnectionProvider() = default;
  [[nodiscard]] virtual LcuConnectionResult Resolve() = 0;
  virtual void Invalidate() noexcept {}
};

// An explicit port/token pair may be inherited through environment variables.
// Otherwise, a configured League root is used to inspect ordinary
// LeagueClientUx logs and accept only a credential set whose PID owns the
// matching loopback listener. Credentials are never accepted on the command
// line or emitted in diagnostics.
class EnvironmentLcuConnectionProvider final : public ILcuConnectionProvider {
 public:
  ~EnvironmentLcuConnectionProvider() override;
  [[nodiscard]] LcuConnectionResult Resolve() override;
  void Invalidate() noexcept override;

 private:
  std::optional<LcuLaunchArguments> cached_log_arguments_;
  std::wstring cached_league_root_;
};

struct LcuHttpResult final {
  bool ok{false};
  std::uint32_t status_code{0U};
  std::string body;
  std::string error_code;
};

class ILcuContextTransport {
 public:
  virtual ~ILcuContextTransport() = default;
  [[nodiscard]] virtual LcuHttpResult Get(const LcuConnection& connection,
                                          LcuEndpoint endpoint) = 0;
};

class WinHttpLcuContextTransport final : public ILcuContextTransport {
 public:
  [[nodiscard]] LcuHttpResult Get(const LcuConnection& connection,
                                  LcuEndpoint endpoint) override;
};

struct LcuContext final {
  std::string gameflow_phase;
  std::optional<std::uint32_t> champion_id;
};

struct LcuContextSnapshot final {
  LcuContextStatus status{LcuContextStatus::Unavailable};
  std::optional<LcuContext> context;
  std::string reason;
  std::chrono::system_clock::time_point observed_at{};
};

struct LcuContextEvent final {
  std::uint64_t sequence{0U};
  LcuContextSnapshot snapshot;
};

[[nodiscard]] std::optional<std::string> ParseGameflowPhase(
    std::string_view json, std::string& reason);

[[nodiscard]] std::optional<std::uint32_t> ParseCurrentChampionId(
    std::string_view json, std::string& reason);

class LcuContextReader final {
 public:
  LcuContextReader(std::unique_ptr<ILcuConnectionProvider> provider,
                   std::unique_ptr<ILcuContextTransport> transport);
  LcuContextReader(const LcuContextReader&) = delete;
  LcuContextReader& operator=(const LcuContextReader&) = delete;

  [[nodiscard]] LcuContextSnapshot Read();

 private:
  std::unique_ptr<ILcuConnectionProvider> provider_;
  std::unique_ptr<ILcuContextTransport> transport_;
};

class LcuContextEmissionGate final {
 public:
  explicit LcuContextEmissionGate(
      std::chrono::milliseconds unchanged_heartbeat);
  [[nodiscard]] bool ShouldEmit(const LcuContextSnapshot& snapshot,
                                std::chrono::steady_clock::time_point now);

 private:
  std::chrono::milliseconds unchanged_heartbeat_;
  std::optional<LcuContextSnapshot> previous_;
  std::optional<std::chrono::steady_clock::time_point> last_emit_;
};

struct LcuContextPollerConfig final {
  std::chrono::milliseconds poll_interval{500};
  std::chrono::milliseconds unchanged_heartbeat{30'000};
};

class LcuContextPoller final {
 public:
  using Callback = std::function<void(const LcuContextEvent&)>;

  LcuContextPoller(std::unique_ptr<LcuContextReader> reader,
                   LcuContextPollerConfig config, Callback callback);
  ~LcuContextPoller();
  LcuContextPoller(const LcuContextPoller&) = delete;
  LcuContextPoller& operator=(const LcuContextPoller&) = delete;

  void Start();
  void Stop() noexcept;
  [[nodiscard]] bool running() const noexcept;

 private:
  void Run() noexcept;

  std::unique_ptr<LcuContextReader> reader_;
  LcuContextPollerConfig config_;
  Callback callback_;
  std::atomic<bool> stop_requested_{false};
  std::atomic<bool> running_{false};
  std::mutex wait_mutex_;
  std::condition_variable wait_condition_;
  std::thread thread_;
};

[[nodiscard]] const char* ToString(LcuContextStatus status) noexcept;
[[nodiscard]] std::string SerializeLcuContextEventJson(
    const LcuContextEvent& event, std::string_view session_id);

}  // namespace lol_assistant::lcu
