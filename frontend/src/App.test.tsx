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
  Marker: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
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

  it("shows a disabled Strava control when OAuth is not configured locally", async () => {
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

    expect(await screen.findByTestId("connect-strava-unavailable")).toHaveTextContent(
      "Strava unavailable"
    );
    expect(screen.getByText(/Add STRAVA_CLIENT_SECRET to enable it\./)).toBeInTheDocument();
    expect(screen.queryByTestId("connect-strava")).not.toBeInTheDocument();
  });

  it("hydrates the connection from callback query parameters and loads dashboard stats", async () => {
    window.history.replaceState(
      {},
      "",
      "/?status=connected&user_id=user-123&display_name=Test%20Athlete&strava_athlete_id=42&sync_enqueued=true"
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
          },
        ]);
      }
      if (url.includes("/users/user-123/dashboard")) {
        return jsonResponse({
          total_munros: 282,
          bagged_munros: 12,
          completion_percentage: 4.3,
          total_ascent_metres: 3450,
          last_bagged_at: "2025-01-02T12:00:00Z",
        });
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    render(<App />);

    expect(await screen.findByTestId("connection-status")).toHaveTextContent(
      "Strava connected. Your latest activities are being synchronised."
    );
    const sidebar = document.getElementById("sidebar");
    expect(sidebar).not.toBeNull();
    expect(await within(sidebar as HTMLElement).findByTestId("bagged-count")).toHaveTextContent("12 / 282");
    expect(await within(sidebar as HTMLElement).findByTestId("total-ascent")).toHaveTextContent("3,450 m");
    expect(window.localStorage.getItem("munrostream.connected-user")).toContain("user-123");
    expect(window.location.search).toBe("");
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
          },
          {
            id: 2,
            name: "Ben Macdui",
            height_metres: 1309,
            latitude: 57.0704,
            longitude: -3.6691,
            is_bagged: false,
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
