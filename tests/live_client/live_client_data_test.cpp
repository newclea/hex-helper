#include <winrt/base.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "lol_assistant/live_client/live_client_data.h"

namespace {

using lol_assistant::live_client::ILiveClientTransport;
using lol_assistant::live_client::LiveClientEmissionGate;
using lol_assistant::live_client::LiveClientEndpoint;
using lol_assistant::live_client::LiveClientEvent;
using lol_assistant::live_client::LiveClientHttpResult;
using lol_assistant::live_client::LiveClientPoller;
using lol_assistant::live_client::LiveClientPollerConfig;
using lol_assistant::live_client::LiveClientReader;
using lol_assistant::live_client::LiveClientSnapshot;
using lol_assistant::live_client::LiveClientStatus;
using lol_assistant::live_client::ParseActivePlayerName;
using lol_assistant::live_client::ParseActivePlayerVitals;
using lol_assistant::live_client::ParsePlayerListForActivePlayer;
using lol_assistant::live_client::SerializeLiveClientEventJson;

void Require(const bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class FixedTransport final : public ILiveClientTransport {
public:
  LiveClientHttpResult active;
  LiveClientHttpResult players;
  LiveClientHttpResult active_player;
  std::vector<LiveClientEndpoint> endpoints;

  [[nodiscard]] LiveClientHttpResult
  Get(const LiveClientEndpoint endpoint) override {
    endpoints.push_back(endpoint);
    if (endpoint == LiveClientEndpoint::ActivePlayerName) {
      return active;
    }
    if (endpoint == LiveClientEndpoint::PlayerList) {
      return players;
    }
    if (endpoint == LiveClientEndpoint::ActivePlayer) {
      return active_player;
    }
    return LiveClientHttpResult{false, 0U, {}, "unexpected_path"};
  }
};

[[nodiscard]] LiveClientHttpResult Ok(std::string body) {
  return LiveClientHttpResult{true, 200U, std::move(body), {}};
}

[[nodiscard]] std::string ValidPlayerList(const double respawn_timer = 12.5) {
  return std::string{
             R"json([{"summonerName":"enemy","championName":"Lux","level":18,"isDead":false,"respawnTimer":0},{"summonerName":"Pilot","riotId":"Pilot#CN1","riotIdGameName":"Pilot","riotIdTagLine":"CN1","championName":"AurelionSol","level":11,"isDead":true,"respawnTimer":)json"} +
         std::to_string(respawn_timer) + "}]";
}

[[nodiscard]] std::string ValidActivePlayer() {
  return R"json({"level":11,"championStats":{"currentHealth":1234.5,"maxHealth":2000.0}})json";
}

void TestStrictParsers() {
  std::string reason;
  const auto active = ParseActivePlayerName(R"json("Pilot")json", reason);
  Require(active == std::optional<std::string>{"Pilot"} && reason.empty(),
          "active player JSON string must parse exactly");
  Require(!ParseActivePlayerName(R"json({"name":"Pilot"})json", reason)
                  .has_value() &&
              reason == "active_player_name_not_string",
          "active player object must fail closed");

  const auto vitals = ParseActivePlayerVitals(ValidActivePlayer(), reason);
  Require(vitals.has_value() && vitals->current_health == 1234.5 &&
              vitals->max_health == 2000.0 && vitals->health_percent == 61.725,
          "active-player championStats health must parse exactly");
  Require(
      !ParseActivePlayerVitals(
           R"json({"championStats":{"currentHealth":"full","maxHealth":2000}})json",
           reason)
              .has_value() &&
          reason == "active_player_health_missing_or_wrong_type",
      "health parser must reject coercion and fail closed");

  const auto player =
      ParsePlayerListForActivePlayer(ValidPlayerList(), "Pilot", reason);
  Require(player.has_value(),
          "player list must match an exact legacy identity");
  Require(player->champion_name == "AurelionSol" && player->level == 11U &&
              player->is_dead && player->respawn_timer_seconds == 12.5,
          "only the requested player's four fields must be returned");

  const auto riot_id =
      ParsePlayerListForActivePlayer(ValidPlayerList(), "Pilot#CN1", reason);
  Require(riot_id.has_value(), "game name plus tag must be a valid identity");
  Require(!ParsePlayerListForActivePlayer(ValidPlayerList(), "pilot", reason)
                  .has_value() &&
              reason == "active_player_not_found",
          "identity matching must not guess through case folding");

  const std::string malformed =
      R"json([{"summonerName":"Pilot","championName":"Ahri","level":"11","isDead":false,"respawnTimer":0}])json";
  Require(
      !ParsePlayerListForActivePlayer(malformed, "Pilot", reason).has_value() &&
          reason == "active_player_fields_missing_or_wrong_type",
      "wrong field types must return UNKNOWN state, never coercion");

  const std::string ambiguous =
      R"json([{"riotId":"Pilot#CN1","championName":"Ahri","level":11,"isDead":false,"respawnTimer":0},{"riotId":"Pilot#CN1","championName":"Lux","level":11,"isDead":false,"respawnTimer":0}])json";
  Require(!ParsePlayerListForActivePlayer(ambiguous, "Pilot#CN1", reason)
                  .has_value() &&
              reason == "active_player_ambiguous",
          "ambiguous own-player identity must fail closed");
}

void TestReaderAndTransportFailure() {
  auto transport = std::make_unique<FixedTransport>();
  auto *const transport_view = transport.get();
  transport->active = Ok(R"json("Pilot")json");
  transport->players = Ok(ValidPlayerList(7.25));
  transport->active_player = Ok(ValidActivePlayer());
  LiveClientReader reader{std::move(transport)};
  const auto snapshot = reader.Read();
  Require(
      snapshot.status == LiveClientStatus::Ready &&
          snapshot.player.has_value() &&
          snapshot.player->respawn_timer_seconds == 7.25 &&
          snapshot.player->current_health == std::optional<double>{1234.5} &&
          snapshot.player->health_percent == std::optional<double>{61.725} &&
          snapshot.reason == "ok",
      "three API responses must fuse into one READY snapshot");
  Require(
      transport_view->endpoints ==
          std::vector<LiveClientEndpoint>{LiveClientEndpoint::ActivePlayerName,
                                          LiveClientEndpoint::PlayerList,
                                          LiveClientEndpoint::ActivePlayer},
      "reader must call only the three allowlisted own-player endpoints");

  auto health_unavailable = std::make_unique<FixedTransport>();
  health_unavailable->active = Ok(R"json("Pilot")json");
  health_unavailable->players = Ok(ValidPlayerList(0.0));
  health_unavailable->active_player =
      LiveClientHttpResult{false, 404U, {}, "http_404"};
  LiveClientReader health_unavailable_reader{std::move(health_unavailable)};
  const auto partial = health_unavailable_reader.Read();
  Require(partial.status == LiveClientStatus::Ready &&
              partial.player.has_value() &&
              !partial.player->current_health.has_value(),
          "brief health endpoint failure must preserve required player state");

  auto unavailable = std::make_unique<FixedTransport>();
  auto *const unavailable_view = unavailable.get();
  unavailable->active =
      LiveClientHttpResult{false, 0U, {}, "connect_failed_12029"};
  LiveClientReader unavailable_reader{std::move(unavailable)};
  const auto failed = unavailable_reader.Read();
  Require(failed.status == LiveClientStatus::Unavailable &&
              !failed.player.has_value() &&
              failed.reason == "active_player_name_connect_failed_12029" &&
              unavailable_view->endpoints.size() == 1U,
          "connection failure must stop before player-list parsing");
}

void TestEmissionGateAndJson() {
  LiveClientSnapshot ready;
  ready.status = LiveClientStatus::Ready;
  ready.reason = "ok";
  ready.observed_at = std::chrono::system_clock::time_point{
      std::chrono::seconds{1'700'000'000}};
  ready.player = lol_assistant::live_client::LiveClientPlayerState{
      "AurelionSol", 11U, true, 9.5};
  ready.player->current_health = 321.0;
  ready.player->max_health = 1234.0;
  ready.player->health_percent = 26.013;

  LiveClientEmissionGate gate{std::chrono::seconds{30}};
  const auto start = std::chrono::steady_clock::time_point{};
  Require(gate.ShouldEmit(ready, start), "first state must be emitted");
  Require(!gate.ShouldEmit(ready, start + std::chrono::seconds{1}),
          "unchanged state must be deduplicated");
  Require(gate.ShouldEmit(ready, start + std::chrono::seconds{30}),
          "unchanged state must have a bounded heartbeat");
  ready.player->level = 12U;
  Require(gate.ShouldEmit(ready, start + std::chrono::seconds{31}),
          "level changes must emit immediately");

  LiveClientEvent event{44U, ready};
  const std::string json = SerializeLiveClientEventJson(event, "session-1");
  Require(json.find("\"type\":\"live_client_state\"") != std::string::npos &&
              json.find("\"status\":\"READY\"") != std::string::npos &&
              json.find("\"championName\":\"AurelionSol\"") !=
                  std::string::npos &&
              json.find("\"level\":12") != std::string::npos &&
              json.find("\"isDead\":true") != std::string::npos &&
              json.find("\"respawnTimer\":9.5") != std::string::npos &&
              json.find("\"currentHealth\":321") != std::string::npos &&
              json.find("\"maxHealth\":1234") != std::string::npos &&
              json.find("\"healthPercent\":26.013") != std::string::npos &&
              json.find("enemy") == std::string::npos &&
              json.find('\n') == std::string::npos,
          "JSONL payload must expose only the own-player contract");
}

void TestPollerLifecycle() {
  auto transport = std::make_unique<FixedTransport>();
  transport->active = Ok(R"json("Pilot")json");
  transport->players = Ok(ValidPlayerList(0.0));
  transport->active_player = Ok(ValidActivePlayer());
  auto reader = std::make_unique<LiveClientReader>(std::move(transport));

  std::mutex mutex;
  std::condition_variable condition;
  std::size_t callbacks = 0U;
  LiveClientPoller poller{std::move(reader),
                          LiveClientPollerConfig{std::chrono::milliseconds{5},
                                                 std::chrono::hours{1}},
                          [&](const LiveClientEvent &event) {
                            std::lock_guard lock(mutex);
                            Require(
                                event.sequence == 1U,
                                "unchanged poller state must be deduplicated");
                            ++callbacks;
                            condition.notify_all();
                          }};
  poller.Start();
  {
    std::unique_lock lock(mutex);
    Require(condition.wait_for(lock, std::chrono::seconds{1},
                               [&] { return callbacks > 0U; }),
            "poller must emit its first read promptly");
  }
  std::this_thread::sleep_for(std::chrono::milliseconds{20});
  poller.Stop();
  Require(!poller.running() && callbacks == 1U,
          "poller stop must join and unchanged snapshots must not flood JSON");
}

} // namespace

int main() {
  bool apartment_initialized = false;
  try {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    apartment_initialized = true;
    TestStrictParsers();
    TestReaderAndTransportFailure();
    TestEmissionGateAndJson();
    TestPollerLifecycle();
    winrt::uninit_apartment();
    std::cout << "live_client_data_test passed: parser=fail_closed; "
                 "own_fields=7; dedup=bounded; poller=joined\n";
    return 0;
  } catch (const std::exception &error) {
    if (apartment_initialized) {
      winrt::uninit_apartment();
    }
    std::cerr << "live_client_data_test failed: " << error.what() << '\n';
    return 1;
  }
}
