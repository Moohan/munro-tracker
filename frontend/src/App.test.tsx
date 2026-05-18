import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("leaflet", () => ({
  default: {
    divIcon: vi.fn(() => ({})),
    latLngBounds: vi.fn(() => ({})),
  },
}));

vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="map">{children}</div>,
  Marker: ({ children }: { children: React.ReactNode }) => <div data-testid="map-marker">{children}</div>,
  Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  useMap: () => ({
    fitBounds: vi.fn(),
    setView: vi.fn(),
  }),
}));

import App from "./App";

const mockFetch = vi.fn();

const jsonResponse = (data: unknown, ok = true) =>
  Promise.resolve({
    ok,
    json: async () => data,
  });

const getRequestUrl = (input: RequestInfo | URL) => {
  if (typeof input === "string") {
    return input;
  }
  if (input instanceof URL) {
    return input.toString();
  }
  return input.url;
};

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch);
    mockFetch.mockReset();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows a Strava connect call to action when disconnected", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connect-strava")).toHaveTextContent("Connect Strava");
    expect(screen.getByTestId("bagged-count")).toHaveTextContent("0 / 1");
    expect(screen.queryByText("OS Outdoor")).not.toBeInTheDocument();
    expect(screen.getByText("OpenTopo")).toBeInTheDocument();
  });

  it("shows the Strava setup form when OAuth is not configured locally", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: false,
          reason: "missing_client_secret",
          message:
            "Strava connection is not available in this local setup yet. Add STRAVA_CLIENT_SECRET to enable it.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("strava-client-id-input")).toBeInTheDocument();
    expect(screen.getByTestId("strava-client-secret-input")).toBeInTheDocument();
    expect(screen.getByText(/Add STRAVA_CLIENT_SECRET to enable it\./)).toBeInTheDocument();
    expect(screen.queryByTestId("connect-strava")).not.toBeInTheDocument();
  });

  it("saves Strava app credentials from the dashboard and enables connect", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: false,
          reason: "missing_client_secret",
          message:
            "Strava connection is not available in this local setup yet. Add STRAVA_CLIENT_SECRET to enable it.",
        });
      }
      if (url.includes("/strava/oauth/settings") && init?.method === "PUT") {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    fireEvent.change(await screen.findByTestId("strava-client-id-input"), {
      target: { value: "12345" },
    });
    fireEvent.change(screen.getByTestId("strava-client-secret-input"), {
      target: { value: "top-secret" },
    });
    fireEvent.click(screen.getByTestId("save-strava-settings"));

    expect(await screen.findByTestId("connect-strava")).toHaveTextContent("Connect Strava");
    expect(screen.getByTestId("connection-status")).toHaveTextContent(
      "Strava app credentials saved. You can now connect Strava."
    );
  });

  it("hydrates the connection from callback query parameters and loads dashboard stats", async () => {
    window.history.replaceState(
      {},
      "",
      "/?status=connected&user_id=user-123&display_name=Test%20Athlete&strava_athlete_id=42&sync_enqueued=true&sync_task_id=task-123"
    );
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 12,
          completion_percentage: 4.3,
          cached_strava_activities: 25,
          total_strava_activities: 220,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/tasks/task-123")) {
        return jsonResponse({
          task_id: "task-123",
          state: "PROGRESS",
          message: "Checked 3 of 12 activities.",
          sync_mode: "latest",
          activities_seen: 12,
          activities_processed: 3,
          activities_with_matches: 1,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 1,
          cached_strava_activities: 25,
          total_strava_activities: null,
          progress_percentage: 25,
          progress_is_indeterminate: false,
          sync_phase: "processing",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:00:15+00:00",
          estimated_remaining_seconds: 45,
          rate_limit_wait_seconds: null,
          rate_limit_scope: null,
          read_window_limit: 100,
          read_window_usage: 12,
          read_window_remaining: 88,
          read_daily_limit: 1000,
          read_daily_usage: 120,
          read_daily_remaining: 880,
          read_window_resets_at: "2026-05-13T13:15:05+00:00",
          read_daily_resets_at: "2026-05-14T00:00:05+00:00",
          retry_after: null,
          is_complete: false,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("sync-progress")).toHaveTextContent(
      "Syncing your latest Strava activities."
    );
    expect(screen.getByTestId("sync-progress")).toHaveTextContent(
      "Processing 3 of 12 downloaded activities"
    );
    expect(screen.getByTestId("sync-progress")).toHaveTextContent("25%");
    const sidebar = document.getElementById("sidebar");
    expect(sidebar).not.toBeNull();
    expect(await within(sidebar as HTMLElement).findByTestId("bagged-count")).toHaveTextContent("12 / 282");
    expect(await within(sidebar as HTMLElement).findByTestId("total-ascent")).toHaveTextContent("3,450 m");
    expect(window.localStorage.getItem("munrostream.connected-user")).toContain("\"pendingSyncTaskId\":\"task-123\"");
    expect(window.localStorage.getItem("munrostream.connected-user")).toContain("\"pendingSyncStartedAt\":");
    expect(window.location.search).toBe("");
  });

  it("lets a connected user manually sync the latest Strava activities", async () => {
    let munroRequests = 0;
    let dashboardRequests = 0;
    let taskPolls = 0;

    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        munroRequests += 1;
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: munroRequests > 1,
            bagged_at: munroRequests > 1 ? "2025-01-02T12:00:00Z" : null,
            source_activity_id: munroRequests > 1 ? 123456789 : null,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        dashboardRequests += 1;
        return jsonResponse({
          total_munros: 282,
          bagged_munros: dashboardRequests > 1 ? 13 : 12,
          completion_percentage: dashboardRequests > 1 ? 4.6 : 4.3,
          cached_strava_activities: dashboardRequests > 1 ? 29 : 25,
          total_strava_activities: dashboardRequests > 1 ? 224 : 220,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: dashboardRequests > 1 ? 3600 : 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/users/user-123/sync?mode=latest") && init?.method === "POST") {
        return jsonResponse({
          user_id: "user-123",
          task_id: "task-456",
          activity_limit: 25,
          sync_mode: "latest",
        });
      }
      if (url.includes("/strava/tasks/task-456")) {
        taskPolls += 1;
        if (taskPolls === 1) {
          return jsonResponse({
            task_id: "task-456",
            state: "PROGRESS",
            message: "Checked 1 of 4 activities.",
            sync_mode: "latest",
            activities_seen: 4,
            activities_processed: 1,
            activities_with_matches: 0,
            activities_skipped_no_polyline: 0,
            activities_skipped_invalid_polyline: 0,
            activities_skipped_unverified_elevation: 0,
            bag_rows_written: 0,
            cached_strava_activities: 26,
            total_strava_activities: 220,
            progress_percentage: 25,
            progress_is_indeterminate: false,
            sync_phase: "processing",
            started_at: "2026-05-13T13:00:00+00:00",
            updated_at: "2026-05-13T13:00:05+00:00",
            estimated_remaining_seconds: 90,
            rate_limit_wait_seconds: null,
            rate_limit_scope: null,
            read_window_limit: 100,
            read_window_usage: 8,
            read_window_remaining: 92,
            read_daily_limit: 1000,
            read_daily_usage: 100,
            read_daily_remaining: 900,
            read_window_resets_at: "2026-05-13T13:15:05+00:00",
            read_daily_resets_at: "2026-05-14T00:00:05+00:00",
            retry_after: null,
            is_complete: false,
            is_error: false,
          });
        }
        return jsonResponse({
          task_id: "task-456",
          state: "SUCCESS",
          message: "Latest Strava sync complete. Checked 4 activities and found 0 Munro matches.",
          sync_mode: "latest",
          activities_seen: 4,
          activities_processed: 4,
          activities_with_matches: 0,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 0,
          cached_strava_activities: 29,
          total_strava_activities: 224,
          progress_percentage: 100,
          progress_is_indeterminate: false,
          sync_phase: "complete",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:01:20+00:00",
          estimated_remaining_seconds: 0,
          rate_limit_wait_seconds: null,
          rate_limit_scope: null,
          read_window_limit: 100,
          read_window_usage: 8,
          read_window_remaining: 92,
          read_daily_limit: 1000,
          read_daily_usage: 100,
          read_daily_remaining: 900,
          read_window_resets_at: "2026-05-13T13:15:05+00:00",
          read_daily_resets_at: "2026-05-14T00:00:05+00:00",
          retry_after: null,
          is_complete: true,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    const syncButton = await screen.findByTestId("sync-latest-activities");
    expect(syncButton).toHaveTextContent("Sync latest activities");

    fireEvent.click(syncButton);

    expect(await screen.findByTestId("sync-progress")).toHaveTextContent(
      "Syncing your latest Strava activities."
    );
    expect(screen.getByTestId("sync-progress")).toHaveTextContent(
      "Processing 1 of 4 downloaded activities"
    );
    expect(screen.getByTestId("sync-latest-activities")).toBeDisabled();

    await waitFor(
      () => {
        expect(screen.getByTestId("connection-status")).toHaveTextContent(
          "Latest Strava sync complete. Checked 4 activities and found 0 Munro matches."
        );
      },
      { timeout: 4000 }
    );

    expect(screen.getByTestId("sync-latest-activities")).not.toBeDisabled();
    expect(munroRequests).toBe(2);
    expect(dashboardRequests).toBe(2);
    expect(window.localStorage.getItem("munrostream.connected-user")).not.toContain(
      "pendingSyncTaskId"
    );
  });

  it("lets a connected user sync all cached Strava history", async () => {
    let munroRequests = 0;
    let dashboardRequests = 0;
    let taskPolls = 0;

    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        munroRequests += 1;
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: munroRequests > 1,
            bagged_at: munroRequests > 1 ? "2025-01-02T12:00:00Z" : null,
            source_activity_id: munroRequests > 1 ? 123456789 : null,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        dashboardRequests += 1;
        return jsonResponse({
          total_munros: 282,
          bagged_munros: dashboardRequests > 1 ? 14 : 13,
          completion_percentage: dashboardRequests > 1 ? 5.0 : 4.6,
          cached_strava_activities: dashboardRequests > 1 ? 30 : 25,
          total_strava_activities: 220,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: dashboardRequests > 1 ? 3750 : 3600,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/users/user-123/sync?mode=full") && init?.method === "POST") {
        return jsonResponse({
          user_id: "user-123",
          task_id: "task-789",
          activity_limit: null,
          sync_mode: "full",
        });
      }
      if (url.includes("/strava/tasks/task-789")) {
        taskPolls += 1;
        if (taskPolls === 1) {
          return jsonResponse({
            task_id: "task-789",
            state: "PROGRESS",
            message: "Checked 2 activities so far while syncing your full Strava history.",
            sync_mode: "full",
            activities_seen: 2,
            activities_processed: 2,
            activities_with_matches: 0,
            activities_skipped_no_polyline: 0,
            activities_skipped_invalid_polyline: 0,
            activities_skipped_unverified_elevation: 0,
            bag_rows_written: 0,
            cached_strava_activities: 25,
            total_strava_activities: 220,
            progress_percentage: 11.4,
            progress_is_indeterminate: false,
            sync_phase: "processing",
            started_at: "2026-05-13T13:00:00+00:00",
            updated_at: "2026-05-13T13:02:00+00:00",
            estimated_remaining_seconds: 150,
            rate_limit_wait_seconds: null,
            rate_limit_scope: null,
            read_window_limit: 100,
            read_window_usage: 14,
            read_window_remaining: 86,
            read_daily_limit: 1000,
            read_daily_usage: 200,
            read_daily_remaining: 800,
            read_window_resets_at: "2026-05-13T13:15:05+00:00",
            read_daily_resets_at: "2026-05-14T00:00:05+00:00",
            retry_after: null,
            is_complete: false,
            is_error: false,
          });
        }
        return jsonResponse({
          task_id: "task-789",
          state: "SUCCESS",
          message: "Full Strava history sync complete. Checked 5 activities and found 1 Munro match.",
          sync_mode: "full",
          activities_seen: 5,
          activities_processed: 5,
          activities_with_matches: 1,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 1,
          cached_strava_activities: 30,
          total_strava_activities: 220,
          progress_percentage: 100,
          progress_is_indeterminate: false,
          sync_phase: "complete",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:05:00+00:00",
          estimated_remaining_seconds: 0,
          rate_limit_wait_seconds: null,
          rate_limit_scope: null,
          read_window_limit: 100,
          read_window_usage: 14,
          read_window_remaining: 86,
          read_daily_limit: 1000,
          read_daily_usage: 200,
          read_daily_remaining: 800,
          read_window_resets_at: "2026-05-13T13:15:05+00:00",
          read_daily_resets_at: "2026-05-14T00:00:05+00:00",
          retry_after: null,
          is_complete: true,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    const syncAllButton = await screen.findByTestId("sync-all-activities");
    expect(syncAllButton).toHaveTextContent("Sync all activities");

    fireEvent.click(syncAllButton);

    expect(await screen.findByTestId("sync-progress")).toHaveTextContent(
      "Syncing your full Strava history."
    );
    expect(screen.getByText("Synced 25 of 220 activities so far")).toBeInTheDocument();
    expect(screen.getByText("Downloaded 2 activities in this pass")).toBeInTheDocument();
    expect(screen.getByText("Processing 2 of 2 downloaded activities")).toBeInTheDocument();
    expect(screen.getByText("About 3 mins remaining")).toBeInTheDocument();
    expect(screen.getByTestId("sync-all-progress-count")).toHaveTextContent(
      "25 / 220 synced"
    );
    expect(screen.getByTestId("sync-all-activities")).toBeDisabled();
    expect(screen.getByTestId("sync-latest-activities")).toBeDisabled();

    await waitFor(
      () => {
        expect(screen.getByTestId("connection-status")).toHaveTextContent(
          "Full Strava history sync complete. Checked 5 activities and found 1 Munro match."
        );
      },
      { timeout: 4000 }
    );

    expect(screen.getByTestId("sync-all-activities")).not.toBeDisabled();
    expect(screen.getByTestId("sync-latest-activities")).not.toBeDisabled();
    expect(munroRequests).toBe(2);
    expect(dashboardRequests).toBe(2);
  });

  it("auto-refreshes Strava totals when the dashboard count is missing", async () => {
    let totalsRefreshRequests = 0;
    let totalsTaskPolls = 0;

    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 12,
          completion_percentage: 4.3,
          cached_strava_activities: 25,
          total_strava_activities: null,
          total_strava_activities_updated_at: null,
          total_ascent_metres: 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/users/user-123/totals/refresh") && init?.method === "POST") {
        totalsRefreshRequests += 1;
        return jsonResponse({
          user_id: "user-123",
          task_id: "task-totals",
          activity_limit: null,
          sync_mode: "totals",
        });
      }
      if (url.includes("/strava/tasks/task-totals")) {
        totalsTaskPolls += 1;
        return jsonResponse({
          task_id: "task-totals",
          state: "PROGRESS",
          message: "Counting your Strava history.",
          sync_mode: "totals",
          activities_seen: 220,
          activities_processed: 220,
          activities_with_matches: 0,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 0,
          cached_strava_activities: 25,
          total_strava_activities: 220,
          progress_percentage: 0,
          progress_is_indeterminate: true,
          sync_phase: "discovering",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:00:15+00:00",
          estimated_remaining_seconds: null,
          rate_limit_wait_seconds: null,
          rate_limit_scope: null,
          read_window_limit: 100,
          read_window_usage: 12,
          read_window_remaining: 88,
          read_daily_limit: 1000,
          read_daily_usage: 120,
          read_daily_remaining: 880,
          read_window_resets_at: "3026-05-13T13:15:05+00:00",
          read_daily_resets_at: "3026-05-14T00:00:05+00:00",
          retry_after: null,
          is_complete: false,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("totals-refresh-status")).toHaveTextContent(
      "Refreshing Strava totals..."
    );
    expect(screen.getByTestId("totals-refresh-status")).toHaveTextContent(
      "Counted 220 activities so far"
    );
    expect(screen.getByTestId("sync-all-progress-count")).toHaveTextContent("25 / 220 synced");
    expect(totalsRefreshRequests).toBe(1);
    expect(totalsTaskPolls).toBeGreaterThan(0);
  });

  it("keeps the full-sync progress visible while a rate-limited retry is pending", async () => {
    let taskPolls = 0;

    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2024-10-30T13:44:23Z",
            source_activity_id: 987654321,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 1,
          completion_percentage: 0.4,
          cached_strava_activities: 98,
          total_strava_activities: 220,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: 562,
          last_bagged_at: "2024-10-30T13:44:23Z",
        });
      }
      if (url.includes("/strava/users/user-123/sync?mode=full") && init?.method === "POST") {
        return jsonResponse({
          user_id: "user-123",
          task_id: "task-retry",
          activity_limit: null,
          sync_mode: "full",
        });
      }
      if (url.includes("/strava/tasks/task-retry")) {
        taskPolls += 1;
        if (taskPolls === 1) {
          return jsonResponse({
            task_id: "task-retry",
            state: "PROGRESS",
            message: "Checked 98 activities so far while syncing your full Strava history.",
            sync_mode: "full",
            activities_seen: 98,
            activities_processed: 98,
            activities_with_matches: 1,
            activities_skipped_no_polyline: 16,
            activities_skipped_invalid_polyline: 0,
            activities_skipped_unverified_elevation: 0,
            bag_rows_written: 1,
            cached_strava_activities: 98,
            total_strava_activities: 220,
            progress_percentage: 44.5,
            progress_is_indeterminate: false,
            sync_phase: "processing",
            started_at: "2026-05-13T13:00:00+00:00",
            updated_at: "2026-05-13T13:10:00+00:00",
            estimated_remaining_seconds: 120,
            rate_limit_wait_seconds: null,
            rate_limit_scope: null,
            read_window_limit: 100,
            read_window_usage: 98,
            read_window_remaining: 2,
            read_daily_limit: 1000,
            read_daily_usage: 740,
            read_daily_remaining: 260,
            read_window_resets_at: "2026-05-13T13:15:05+00:00",
            read_daily_resets_at: "2026-05-14T00:00:05+00:00",
            retry_after: null,
            is_complete: false,
            is_error: false,
          });
        }
        return jsonResponse({
          task_id: "task-retry",
          state: "RETRY",
          message: "Waiting for Strava read limits.",
          sync_mode: "full",
          activities_seen: 120,
          activities_processed: 98,
          activities_with_matches: 1,
          activities_skipped_no_polyline: 16,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 1,
          cached_strava_activities: 98,
          total_strava_activities: 220,
          progress_percentage: 44.5,
          progress_is_indeterminate: false,
          sync_phase: "waiting_for_rate_limit",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:10:00+00:00",
          estimated_remaining_seconds: 360,
          rate_limit_wait_seconds: 240,
          rate_limit_scope: "short_window",
          read_window_limit: 100,
          read_window_usage: 100,
          read_window_remaining: 0,
          read_daily_limit: 1000,
          read_daily_usage: 742,
          read_daily_remaining: 258,
          read_window_resets_at: "3026-05-13T13:14:45+00:00",
          read_daily_resets_at: "3026-05-14T00:00:05+00:00",
          retry_after: "2026-05-13T13:14:00+00:00",
          is_complete: false,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    fireEvent.click(await screen.findByTestId("sync-all-activities"));

    await waitFor(() => {
      expect(screen.getByTestId("sync-progress")).toHaveTextContent(
        "15-minute Strava read limit reached."
      );
    }, { timeout: 4000 });

    expect(screen.getByText(/Resets in/)).toBeInTheDocument();
    expect(screen.getByText("Reads left this window: 0 / 100")).toBeInTheDocument();
    expect(screen.getByText("Today: 742 / 1,000 used")).toBeInTheDocument();
    expect(screen.getByTestId("sync-all-progress-count")).toHaveTextContent(
      "98 / 220 synced"
    );
  });

  it("shows daily Strava read-limit messaging when the daily quota is exhausted", async () => {
    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 12,
          completion_percentage: 4.3,
          cached_strava_activities: 220,
          total_strava_activities: 282,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/users/user-123/sync?mode=full") && init?.method === "POST") {
        return jsonResponse({
          user_id: "user-123",
          task_id: "task-daily-limit",
          activity_limit: null,
          sync_mode: "full",
        });
      }
      if (url.includes("/strava/tasks/task-daily-limit")) {
        return jsonResponse({
          task_id: "task-daily-limit",
          state: "RETRY",
          message: "Waiting for Strava read limits.",
          sync_mode: "full",
          activities_seen: 240,
          activities_processed: 220,
          activities_with_matches: 4,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 4,
          cached_strava_activities: 220,
          total_strava_activities: 282,
          progress_percentage: 78,
          progress_is_indeterminate: false,
          sync_phase: "waiting_for_rate_limit",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:10:00+00:00",
          estimated_remaining_seconds: 7200,
          rate_limit_wait_seconds: 5400,
          rate_limit_scope: "daily",
          read_window_limit: 100,
          read_window_usage: 88,
          read_window_remaining: 12,
          read_daily_limit: 1000,
          read_daily_usage: 1000,
          read_daily_remaining: 0,
          read_window_resets_at: "3026-05-13T13:15:05+00:00",
          read_daily_resets_at: "3026-05-14T00:00:05+00:00",
          retry_after: "3026-05-14T00:00:05+00:00",
          is_complete: false,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    fireEvent.click(await screen.findByTestId("sync-all-activities"));

    expect(await screen.findByTestId("sync-progress")).toHaveTextContent(
      "Daily Strava read limit reached."
    );
    expect(screen.getByText(/Resets at midnight UTC/)).toBeInTheDocument();
    expect(screen.getByText("Today: 1,000 / 1,000 used")).toBeInTheDocument();
  });

  it("clears a stale pending Strava sync and shows a retry message", async () => {
    window.localStorage.setItem(
      "munrostream.connected-user",
      JSON.stringify({
        userId: "user-123",
        displayName: "Test Athlete",
        stravaAthleteId: "42",
        pendingSyncTaskId: "task-stale",
        pendingSyncStartedAt: Date.now() - 31_000,
      })
    );

    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 12,
          completion_percentage: 4.3,
          cached_strava_activities: 12,
          total_strava_activities: 220,
          total_strava_activities_updated_at: "2026-05-13T09:30:00Z",
          total_ascent_metres: 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      if (url.includes("/strava/tasks/task-stale")) {
        return jsonResponse({
          task_id: "task-stale",
          state: "PENDING",
          message: "Waiting for the Strava sync to start.",
          sync_mode: "latest",
          activities_seen: 0,
          activities_processed: 0,
          activities_with_matches: 0,
          activities_skipped_no_polyline: 0,
          activities_skipped_invalid_polyline: 0,
          activities_skipped_unverified_elevation: 0,
          bag_rows_written: 0,
          cached_strava_activities: 12,
          total_strava_activities: 220,
          progress_percentage: 0,
          progress_is_indeterminate: false,
          sync_phase: "pending",
          started_at: "2026-05-13T13:00:00+00:00",
          updated_at: "2026-05-13T13:00:00+00:00",
          estimated_remaining_seconds: null,
          rate_limit_wait_seconds: null,
          rate_limit_scope: null,
          read_window_limit: null,
          read_window_usage: null,
          read_window_remaining: null,
          read_daily_limit: null,
          read_daily_usage: null,
          read_daily_remaining: null,
          read_window_resets_at: null,
          read_daily_resets_at: null,
          retry_after: null,
          is_complete: false,
          is_error: false,
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connection-status")).toHaveTextContent(
      "The Strava sync did not start in time. Try syncing the latest activities again."
    );
    expect(screen.queryByTestId("sync-progress")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("munrostream.connected-user")).not.toContain(
      "pendingSyncTaskId"
    );
  });

  it("shows a Strava error banner and clears the query string", async () => {
    window.history.replaceState(
      {},
      "",
      "/?status=error&error_code=missing_client_secret&error_message=Strava%20connection%20is%20not%20available%20in%20this%20local%20setup%20yet."
    );
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: false,
          reason: "missing_client_secret",
          message:
            "Strava connection is not available in this local setup yet. Add STRAVA_CLIENT_SECRET to enable it.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connection-status")).toHaveTextContent(
      "Strava connection is not available in this local setup yet."
    );
    expect(window.location.search).toBe("");
  });

  it("filters the Munro list by search term", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
          {
            id: 2,
            name: "Ben Macdui",
            height_metres: 1309,
            latitude: 57.0704,
            longitude: -3.6691,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);
    await screen.findByTestId("connect-strava");
    const sidebar = document.getElementById("sidebar");
    expect(sidebar).not.toBeNull();
    expect(await within(sidebar as HTMLElement).findByText("Ben Nevis")).toBeInTheDocument();
    fireEvent.change(within(sidebar as HTMLElement).getByPlaceholderText("Search Munros"), {
      target: { value: "Macdui" },
    });

    await waitFor(() => {
      expect(within(sidebar as HTMLElement).queryByText("Ben Nevis")).not.toBeInTheDocument();
      expect(within(sidebar as HTMLElement).getByText("Ben Macdui")).toBeInTheDocument();
    });
  });

  it("keeps the sidebar scrollable while the controls and Munro list are visible", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
          {
            id: 2,
            name: "Ben More",
            height_metres: 1174,
            latitude: 56.3852,
            longitude: -4.5427,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    await screen.findByTestId("connect-strava");
    const sidebar = document.getElementById("sidebar");
    expect(sidebar).not.toBeNull();
    expect(sidebar).toHaveClass("overflow-y-auto");
    expect(within(sidebar as HTMLElement).getByPlaceholderText("Search Munros")).toBeInTheDocument();
    expect(within(sidebar as HTMLElement).getByText("Ben Nevis")).toBeInTheDocument();
  });

  it("filters map markers separately from the sidebar list", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
          },
          {
            id: 2,
            name: "Ben Macdui",
            height_metres: 1309,
            latitude: 57.0704,
            longitude: -3.6691,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connect-strava")).toBeInTheDocument();
    expect(screen.getAllByTestId("map-marker")).toHaveLength(2);
    expect(screen.getAllByText("Ben Nevis")).toHaveLength(2);
    expect(screen.getAllByText("Ben Macdui")).toHaveLength(2);

    fireEvent.click(screen.getByTestId("map-filter-bagged"));

    expect(screen.getAllByTestId("map-marker")).toHaveLength(1);
    expect(screen.getAllByText("Ben Nevis")).toHaveLength(2);
    expect(screen.getAllByText("Ben Macdui")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("map-filter-unbagged"));

    expect(screen.getAllByTestId("map-marker")).toHaveLength(1);
    expect(screen.getAllByText("Ben Nevis")).toHaveLength(1);
    expect(screen.getAllByText("Ben Macdui")).toHaveLength(2);

    fireEvent.click(screen.getByTestId("map-filter-all"));

    expect(screen.getAllByTestId("map-marker")).toHaveLength(2);
  });

  it("shows repeat bag counts and featured Strava activities on bagged Munro popups", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([
          {
            id: 1,
            name: "Ben Nevis",
            height_metres: 1345,
            latitude: 56.7968,
            longitude: -5.0035,
            is_bagged: true,
            bagged_at: "2025-01-02T12:00:00Z",
            source_activity_id: 123456789,
            bag_count: 4,
            bag_activities: [
              {
                source_activity_id: 123456789,
                name: "First Winter Round",
                activity_date: "2025-01-02T12:00:00Z",
              },
              {
                source_activity_id: 234567890,
                name: "Return from the Glen",
                activity_date: "2025-03-14T08:30:00Z",
              },
              {
                source_activity_id: 345678901,
                name: "Sunrise Summit Lap",
                activity_date: "2025-05-09T07:31:14Z",
              },
            ],
          },
          {
            id: 2,
            name: "Ben Macdui",
            height_metres: 1309,
            latitude: 57.0704,
            longitude: -3.6691,
            is_bagged: false,
            bagged_at: null,
            source_activity_id: null,
            bag_count: 0,
            bag_activities: [],
          },
        ]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connect-strava")).toBeInTheDocument();
    expect(screen.getByText("4x")).toBeInTheDocument();
    expect(screen.getByText("Bagged on 2 Jan 2025")).toBeInTheDocument();
    expect(screen.getByText("Bagged 4 times")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "First Winter Round (2 Jan 25)" })).toHaveAttribute(
      "href",
      "https://www.strava.com/activities/123456789"
    );
    expect(
      screen.getByRole("link", { name: "Return from the Glen (14 Mar 25)" })
    ).toHaveAttribute("href", "https://www.strava.com/activities/234567890");
    expect(
      screen.getByRole("link", { name: "Sunrise Summit Lap (9 May 25)" })
    ).toHaveAttribute("href", "https://www.strava.com/activities/345678901");
  });

  it("shows an explicit seed error when the Munro dataset is empty", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse([]);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Munro data has not been loaded yet. Restart the stack and check the munro-seed service."
      )
    ).toBeInTheDocument();
  });

  it("shows an error state when the API request fails", async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = getRequestUrl(input);
      if (url.includes("/munros")) {
        return jsonResponse({}, false);
      }
      if (url.includes("/strava/oauth/status")) {
        return jsonResponse({
          oauth_available: true,
          reason: null,
          message: "Strava OAuth is available.",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Failed to load Munros.")).toBeInTheDocument();
  });
});
