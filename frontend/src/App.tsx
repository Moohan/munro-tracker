import React, { useEffect, useMemo, useState } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const CONNECTION_STORAGE_KEY = "munrostream.connected-user";
const OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TOPO_TILE_URL = "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png";
const OS_TILE_URL_TEMPLATE =
  import.meta.env.VITE_OS_MAPS_TILE_URL_TEMPLATE ||
  "https://api.os.uk/maps/raster/v1/zxy/Outdoor_3857/{z}/{x}/{y}.png?key={key}";
const OS_TILE_ATTRIBUTION =
  import.meta.env.VITE_OS_MAPS_ATTRIBUTION ||
  "Contains OS data &copy; Crown copyright and database rights";
const OS_TILE_API_KEY = import.meta.env.VITE_OS_MAPS_API_KEY || "";

const baggedPeakIcon = L.divIcon({
  html: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 4L4 18H20L12 4Z" fill="#15803d" stroke="white" stroke-width="2" stroke-linejoin="round"/>
    </svg>
  `,
  className: "custom-peak-icon",
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -20],
});

const remainingPeakIcon = L.divIcon({
  html: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 4L4 18H20L12 4Z" fill="#dc2626" stroke="white" stroke-width="2" stroke-linejoin="round"/>
    </svg>
  `,
  className: "custom-peak-icon",
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -20],
});

type BasemapMode = "os" | "topo" | "osm";

type Munro = {
  id: number;
  name: string;
  height_metres: number;
  latitude: number;
  longitude: number;
  is_bagged: boolean;
};

type DashboardSummary = {
  total_munros: number;
  bagged_munros: number;
  completion_percentage: number;
  total_ascent_metres: number;
  last_bagged_at: string | null;
};

type StravaOAuthStatus = {
  oauth_available: boolean;
  reason: "missing_client_id" | "missing_client_secret" | null;
  message: string;
};

type StoredConnection = {
  userId: string;
  displayName: string | null;
  stravaAthleteId: string | null;
};

const hasOsBasemap = Boolean(OS_TILE_API_KEY && OS_TILE_URL_TEMPLATE);
const getPeakIcon = (isBagged: boolean) => (isBagged ? baggedPeakIcon : remainingPeakIcon);

const formatMetres = (value: number) => `${Math.round(value).toLocaleString("en-GB")} m`;

const formatLastBagDate = (value: string | null) => {
  if (!value) {
    return "No summit recorded yet";
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
};

const readStoredConnection = (): StoredConnection | null => {
  if (typeof window === "undefined") {
    return null;
  }

  const rawValue = window.localStorage.getItem(CONNECTION_STORAGE_KEY);
  if (!rawValue) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawValue) as StoredConnection;
    if (!parsed.userId) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
};

const persistConnection = (connection: StoredConnection | null) => {
  if (typeof window === "undefined") {
    return;
  }

  if (connection === null) {
    window.localStorage.removeItem(CONNECTION_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(CONNECTION_STORAGE_KEY, JSON.stringify(connection));
};

const buildStravaLoginUrl = () => {
  if (typeof window === "undefined") {
    return `${API_BASE_URL}/strava/oauth/login`;
  }

  const nextUrl = new URL(window.location.href);
  nextUrl.searchParams.delete("status");
  nextUrl.searchParams.delete("user_id");
  nextUrl.searchParams.delete("display_name");
  nextUrl.searchParams.delete("strava_athlete_id");
  nextUrl.searchParams.delete("sync_enqueued");
  nextUrl.searchParams.delete("sync_task_id");
  nextUrl.searchParams.delete("error_code");
  nextUrl.searchParams.delete("error_message");
  return `${API_BASE_URL}/strava/oauth/login?next_url=${encodeURIComponent(nextUrl.toString())}`;
};

const getOsTileUrl = () => OS_TILE_URL_TEMPLATE.replace("{key}", encodeURIComponent(OS_TILE_API_KEY));

const SetMapBounds = ({ munros }: { munros: Munro[] }) => {
  const map = useMap();

  useEffect(() => {
    if (munros.length === 0) {
      return;
    }

    const bounds = L.latLngBounds(munros.map((munro) => [munro.latitude, munro.longitude]));
    map.fitBounds(bounds, { padding: [50, 50] });
  }, [map, munros]);

  return null;
};

const MapController = ({ targetMunro }: { targetMunro: Munro | null }) => {
  const map = useMap();

  useEffect(() => {
    if (!targetMunro) {
      return;
    }

    map.setView([targetMunro.latitude, targetMunro.longitude], 12, { animate: true });
  }, [map, targetMunro]);

  return null;
};

const App: React.FC = () => {
  const [munros, setMunros] = useState<Munro[]>([]);
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [connection, setConnection] = useState<StoredConnection | null>(() => readStoredConnection());
  const [stravaOAuthStatus, setStravaOAuthStatus] = useState<StravaOAuthStatus | null>(null);
  const [connectionReady, setConnectionReady] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"success" | "error" | "info">("success");
  const [loadingMunros, setLoadingMunros] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [mapType, setMapType] = useState<BasemapMode>(hasOsBasemap ? "os" : "topo");
  const [searchQuery, setSearchQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "bagged" | "remaining">("all");
  const [sortBy, setSortBy] = useState<"height" | "alphabetical">("height");
  const [selectedMunro, setSelectedMunro] = useState<Munro | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");

    if (status === "connected" && params.get("user_id")) {
      const nextConnection = {
        userId: params.get("user_id") ?? "",
        displayName: params.get("display_name"),
        stravaAthleteId: params.get("strava_athlete_id"),
      };
      persistConnection(nextConnection);
      setConnection(nextConnection);

      const syncEnqueued = params.get("sync_enqueued") === "true";
      setStatusTone("success");
      setStatusMessage(
        syncEnqueued
          ? "Strava connected. Your latest activities are being synchronised."
          : "Strava connected. You can trigger a sync again at any time."
      );
    } else if (status === "error") {
      setStatusTone("error");
      setStatusMessage(
        params.get("error_message") || "Unable to start the Strava connection right now."
      );
    }

    [
      "status",
      "user_id",
      "display_name",
      "strava_athlete_id",
      "sync_enqueued",
      "sync_task_id",
      "error_code",
      "error_message",
    ].forEach((key) => params.delete(key));
    const nextUrl = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}${window.location.hash}`;
    window.history.replaceState({}, "", nextUrl);
    setConnectionReady(true);
  }, []);

  useEffect(() => {
    if (!connectionReady) {
      return;
    }

    const controller = new AbortController();

    const fetchMunros = async () => {
      try {
        setLoadingMunros(true);
        const searchParams = new URLSearchParams({ limit: "300" });
        if (connection?.userId) {
          searchParams.set("user_id", connection.userId);
        }

        const response = await fetch(`${API_BASE_URL}/munros?${searchParams.toString()}`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Failed to load Munros.");
        }

        const payload = (await response.json()) as Munro[];
        if (payload.length === 0) {
          throw new Error(
            "Munro data has not been loaded yet. Restart the stack and check the munro-seed service."
          );
        }
        setMunros(payload);
        setError(null);
      } catch (requestError) {
        if (!controller.signal.aborted) {
          setError(requestError instanceof Error ? requestError.message : "Unable to load Munros.");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoadingMunros(false);
        }
      }
    };

    void fetchMunros();
    return () => controller.abort();
  }, [connection, connectionReady]);

  useEffect(() => {
    if (!connectionReady) {
      return;
    }

    if (connection) {
      setStravaOAuthStatus(null);
      return;
    }

    const controller = new AbortController();

    const fetchStravaStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/strava/oauth/status`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Failed to load Strava status.");
        }

        const payload = (await response.json()) as StravaOAuthStatus;
        setStravaOAuthStatus(payload);
      } catch {
        if (!controller.signal.aborted) {
          setStravaOAuthStatus(null);
        }
      }
    };

    void fetchStravaStatus();
    return () => controller.abort();
  }, [connection, connectionReady]);

  useEffect(() => {
    if (!connectionReady) {
      return;
    }

    if (!connection?.userId) {
      setDashboard(null);
      return;
    }

    const controller = new AbortController();

    const fetchDashboard = async () => {
      try {
        setLoadingDashboard(true);
        const response = await fetch(`${API_BASE_URL}/users/${connection.userId}/dashboard`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Failed to load dashboard statistics.");
        }

        const payload = (await response.json()) as DashboardSummary;
        setDashboard(payload);
      } catch (requestError) {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "Unable to load dashboard statistics."
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoadingDashboard(false);
        }
      }
    };

    void fetchDashboard();
    return () => controller.abort();
  }, [connection, connectionReady]);

  const displayedMunros = useMemo(() => {
    return munros
      .filter((munro) => {
        const matchesSearch = munro.name.toLowerCase().includes(searchQuery.toLowerCase());
        const matchesFilter =
          filter === "all" ||
          (filter === "bagged" && munro.is_bagged) ||
          (filter === "remaining" && !munro.is_bagged);
        return matchesSearch && matchesFilter;
      })
      .sort((left, right) => {
        if (sortBy === "height") {
          return right.height_metres - left.height_metres;
        }
        return left.name.localeCompare(right.name);
      });
  }, [filter, munros, searchQuery, sortBy]);

  const summary = useMemo(() => {
    return {
      totalMunros: dashboard?.total_munros ?? munros.length,
      baggedMunros: dashboard?.bagged_munros ?? 0,
      completionPercentage: dashboard?.completion_percentage ?? 0,
      totalAscentMetres: dashboard?.total_ascent_metres ?? 0,
      lastBaggedAt: dashboard?.last_bagged_at ?? null,
    };
  }, [dashboard, munros.length]);

  const mapOptions = useMemo(() => {
    const options: Array<{ mode: BasemapMode; label: string; url: string; attribution: string }> = [];
    if (hasOsBasemap) {
      options.push({
        mode: "os",
        label: "OS Outdoor",
        url: getOsTileUrl(),
        attribution: OS_TILE_ATTRIBUTION,
      });
    }
    options.push(
      {
        mode: "topo",
        label: "OpenTopo",
        url: TOPO_TILE_URL,
        attribution: "&copy; OpenTopoMap contributors",
      },
      {
        mode: "osm",
        label: "OpenStreetMap",
        url: OSM_TILE_URL,
        attribution: "&copy; OpenStreetMap contributors",
      }
    );
    return options;
  }, []);

  const activeMap = mapOptions.find((option) => option.mode === mapType) ?? mapOptions[0];
  const isStravaUnavailable = Boolean(
    !connection && stravaOAuthStatus && !stravaOAuthStatus.oauth_available
  );

  if (loadingMunros) {
    return (
      <div className="flex h-screen items-center justify-center bg-stone-100">
        <div className="text-center">
          <div className="mx-auto mb-4 h-12 w-12 animate-spin rounded-full border-4 border-emerald-800 border-t-transparent" />
          <p className="font-semibold text-emerald-950">Loading the Munros...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-stone-100 p-6">
        <div className="max-w-md rounded-3xl border border-rose-200 bg-white p-8 text-center shadow-xl">
          <h2 className="mb-2 text-2xl font-black text-rose-700">Something went wrong</h2>
          <p className="mb-6 text-sm text-stone-600">{error}</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-xl bg-emerald-800 px-6 py-2 text-sm font-bold text-white transition hover:bg-emerald-900"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-stone-100 text-stone-900">
      {isSidebarOpen && (
        <div
          className="fixed inset-0 z-[1001] bg-stone-950/45 backdrop-blur-sm lg:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <aside
        id="sidebar"
        className={`fixed inset-y-0 left-0 z-[1002] w-96 transform bg-[#fffaf1] p-6 shadow-2xl transition-transform duration-300 ease-in-out lg:static lg:translate-x-0 ${isSidebarOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex h-full flex-col">
          <div className="mb-6">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.3em] text-emerald-800/70">
                  Scottish hill bagging
                </p>
                <h1 className="mt-1 text-3xl font-black tracking-tight text-emerald-950">
                  MunroStream
                </h1>
              </div>
              <button
                type="button"
                onClick={() => setIsSidebarOpen(false)}
                className="text-stone-400 transition hover:text-stone-700 lg:hidden"
                aria-label="Close sidebar"
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            {statusMessage ? (
              <div
                data-testid="connection-status"
                className={`mb-4 rounded-2xl px-4 py-3 text-sm ${
                  statusTone === "error"
                    ? "border border-rose-200 bg-rose-50 text-rose-900"
                    : statusTone === "info"
                      ? "border border-stone-200 bg-stone-100 text-stone-800"
                      : "border border-emerald-200 bg-emerald-50 text-emerald-950"
                }`}
              >
                {statusMessage}
              </div>
            ) : null}

            <div className="rounded-[28px] bg-gradient-to-br from-emerald-950 via-emerald-900 to-lime-800 p-5 text-white shadow-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] font-black uppercase tracking-[0.25em] text-emerald-100/80">
                    Progress
                  </p>
                  <p data-testid="bagged-count" className="mt-2 text-3xl font-black">
                    {summary.baggedMunros} / {summary.totalMunros}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-[11px] font-black uppercase tracking-[0.25em] text-emerald-100/80">
                    Completion
                  </p>
                  <p className="mt-2 text-xl font-black">
                    {summary.completionPercentage.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/20">
                <div
                  className="h-full rounded-full bg-lime-300 transition-all duration-700"
                  style={{ width: `${summary.completionPercentage}%` }}
                />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-2xl bg-white/10 p-3">
                  <p className="text-[10px] font-black uppercase tracking-[0.2em] text-emerald-100/80">
                    Total ascent
                  </p>
                  <p data-testid="total-ascent" className="mt-2 font-bold">
                    {loadingDashboard && connection ? "Loading..." : formatMetres(summary.totalAscentMetres)}
                  </p>
                </div>
                <div className="rounded-2xl bg-white/10 p-3">
                  <p className="text-[10px] font-black uppercase tracking-[0.2em] text-emerald-100/80">
                    Last bag
                  </p>
                  <p data-testid="last-bagged-at" className="mt-2 font-bold">
                    {loadingDashboard && connection ? "Loading..." : formatLastBagDate(summary.lastBaggedAt)}
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="mb-5 rounded-3xl border border-stone-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.25em] text-stone-500">
                  Strava
                </p>
                <p className="mt-2 text-sm text-stone-700">
                  {connection
                    ? `Connected as ${connection.displayName || "your athlete profile"}.`
                    : isStravaUnavailable
                      ? stravaOAuthStatus?.message
                      : "Connect Strava to sync bagged Munros, total ascent and your latest summit date."}
                </p>
              </div>
              {connection ? (
                <button
                  type="button"
                  onClick={() => {
                    persistConnection(null);
                    setConnection(null);
                    setStatusTone("info");
                    setStatusMessage("Local Strava session cleared from this browser.");
                  }}
                  className="rounded-xl border border-stone-200 px-3 py-2 text-xs font-black uppercase tracking-[0.2em] text-stone-600 transition hover:border-stone-300 hover:text-stone-900"
                >
                  Clear
                </button>
              ) : null}
            </div>
            {!connection && isStravaUnavailable ? (
              <button
                type="button"
                data-testid="connect-strava-unavailable"
                disabled
                className="mt-4 inline-flex cursor-not-allowed rounded-xl bg-stone-300 px-4 py-3 text-sm font-black text-stone-600"
              >
                Strava unavailable
              </button>
            ) : null}
            {!connection && !isStravaUnavailable ? (
              <a
                data-testid="connect-strava"
                href={buildStravaLoginUrl()}
                className="mt-4 inline-flex rounded-xl bg-orange-500 px-4 py-3 text-sm font-black text-white transition hover:bg-orange-600"
              >
                Connect Strava
              </a>
            ) : null}
          </div>

          <div className="mb-5 space-y-4">
            <div className="relative">
              <input
                type="text"
                placeholder="Search Munros"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="w-full rounded-2xl border border-transparent bg-white px-4 py-3 pl-10 text-sm shadow-sm outline-none transition focus:border-emerald-300"
              />
              <svg
                className="absolute left-3 top-3.5 text-stone-400"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="8" />
                <path d="M21 21l-4.35-4.35" />
              </svg>
            </div>

            <div className="grid grid-cols-3 gap-2">
              {(["all", "bagged", "remaining"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  className={`rounded-xl py-2 text-xs font-black uppercase tracking-[0.18em] transition ${
                    filter === value
                      ? "bg-emerald-900 text-white shadow-md"
                      : "bg-white text-stone-500 shadow-sm hover:text-stone-900"
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>

            <div className="flex items-center justify-between px-1 text-[10px] font-black uppercase tracking-[0.25em] text-stone-500">
              <span>Sort</span>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setSortBy("height")}
                  className={sortBy === "height" ? "text-emerald-900" : ""}
                >
                  Height
                </button>
                <button
                  type="button"
                  onClick={() => setSortBy("alphabetical")}
                  className={sortBy === "alphabetical" ? "text-emerald-900" : ""}
                >
                  A-Z
                </button>
              </div>
            </div>
          </div>

          <div className="-mx-2 flex-1 space-y-2 overflow-y-auto px-2 custom-scrollbar">
            {displayedMunros.map((munro) => (
              <button
                key={munro.id}
                type="button"
                onClick={() => {
                  setSelectedMunro(munro);
                  if (window.innerWidth < 1024) {
                    setIsSidebarOpen(false);
                  }
                }}
                className="flex w-full items-center justify-between rounded-2xl border border-transparent bg-white p-3 text-left shadow-sm transition hover:border-stone-200 hover:bg-stone-50"
              >
                <div>
                  <h3 className="text-sm font-bold text-stone-900">{munro.name}</h3>
                  <p className="text-xs text-stone-500">{formatMetres(munro.height_metres)}</p>
                </div>
                <div className={`h-2.5 w-2.5 rounded-full ${munro.is_bagged ? "bg-emerald-600" : "bg-rose-500"}`} />
              </button>
            ))}
            {displayedMunros.length === 0 ? (
              <p className="py-8 text-center text-sm text-stone-500">No Munros match that search.</p>
            ) : null}
          </div>
        </div>
      </aside>

      <main className="relative flex-1">
        <button
          type="button"
          onClick={() => setIsSidebarOpen(true)}
          className="absolute left-4 top-4 z-[1000] rounded-2xl bg-white p-3 shadow-xl hover:bg-stone-50 lg:hidden"
          aria-label="Open menu"
          aria-controls="sidebar"
          aria-expanded={isSidebarOpen}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 12h18M3 6h18M3 18h18" />
          </svg>
        </button>

        <div className="absolute right-4 top-4 z-[1000] flex flex-wrap gap-2 rounded-[22px] bg-white/90 p-2 shadow-xl backdrop-blur">
          {mapOptions.map((option) => (
            <button
              key={option.mode}
              type="button"
              onClick={() => setMapType(option.mode)}
              className={`rounded-2xl px-4 py-2 text-xs font-black uppercase tracking-[0.18em] transition ${
                mapType === option.mode
                  ? "bg-emerald-900 text-white"
                  : "text-stone-600 hover:bg-stone-100"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <MapContainer
          center={[56.817, -4.183]}
          zoom={7}
          style={{ height: "100%", width: "100%" }}
          zoomControl={false}
        >
          <TileLayer attribution={activeMap.attribution} url={activeMap.url} />
          <SetMapBounds munros={munros} />
          <MapController targetMunro={selectedMunro} />
          {munros.map((munro) => (
            <Marker
              key={munro.id}
              position={[munro.latitude, munro.longitude]}
              icon={getPeakIcon(munro.is_bagged)}
            >
              <Popup className="custom-popup">
                <div className="p-1">
                  <h3 className="mb-1 text-base font-black text-emerald-950">{munro.name}</h3>
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-sm font-bold text-stone-500">
                      {formatMetres(munro.height_metres)}
                    </span>
                    <span
                      className={`text-[10px] font-black uppercase tracking-[0.2em] ${
                        munro.is_bagged ? "text-emerald-700" : "text-rose-600"
                      }`}
                    >
                      {munro.is_bagged ? "Bagged" : "Remaining"}
                    </span>
                  </div>
                </div>
              </Popup>
            </Marker>
          ))}
        </MapContainer>
      </main>
    </div>
  );
};

export default App;
