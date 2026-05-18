import React, { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const CONNECTION_STORAGE_KEY = "munrostream.connected-user";
const PENDING_SYNC_TIMEOUT_MS = 30_000;
const STRAVA_TOTAL_STALE_AFTER_MS = 24 * 60 * 60 * 1000;
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
type MapMunroFilter = "all" | "bagged" | "unbagged";

type MunroBagActivity = {
  source_activity_id: number;
  name: string | null;
  activity_date: string;
};

type Munro = {
  id: number;
  name: string;
  height_metres: number;
  latitude: number;
  longitude: number;
  is_bagged: boolean;
  bagged_at?: string | null;
  source_activity_id?: number | null;
  bag_count?: number;
  bag_activities?: MunroBagActivity[];
};

type DashboardSummary = {
  total_munros: number;
  bagged_munros: number;
  completion_percentage: number;
  cached_strava_activities: number;
  total_strava_activities: number | null;
  total_strava_activities_updated_at: string | null;
  total_ascent_metres: number;
  last_bagged_at: string | null;
};

type StravaOAuthStatus = {
  oauth_available: boolean;
  reason: "missing_client_id" | "missing_client_secret" | null;
  message: string;
};

type StravaOAuthSettingsInput = {
  client_id: number;
  client_secret: string;
};

type SyncMode = "latest" | "full" | "activity" | "totals";
type ManualSyncMode = Exclude<SyncMode, "activity" | "totals">;
type SyncPhase =
  | "pending"
  | "preparing"
  | "discovering"
  | "downloading"
  | "processing"
  | "waiting_for_rate_limit"
  | "complete"
  | "failed";
type RateLimitScope = "short_window" | "daily";

type StravaSyncTaskStatus = {
  task_id: string;
  state: string;
  message: string;
  sync_mode: SyncMode | null;
  activities_seen: number;
  activities_processed: number;
  activities_with_matches: number;
  activities_skipped_no_polyline: number;
  activities_skipped_invalid_polyline: number;
  activities_skipped_unverified_elevation: number;
  bag_rows_written: number;
  cached_strava_activities: number | null;
  total_strava_activities: number | null;
  progress_percentage: number;
  progress_is_indeterminate: boolean;
  sync_phase: SyncPhase | null;
  started_at: string | null;
  updated_at: string | null;
  estimated_remaining_seconds: number | null;
  rate_limit_wait_seconds: number | null;
  rate_limit_scope: RateLimitScope | null;
  read_window_limit: number | null;
  read_window_usage: number | null;
  read_window_remaining: number | null;
  read_daily_limit: number | null;
  read_daily_usage: number | null;
  read_daily_remaining: number | null;
  read_window_resets_at: string | null;
  read_daily_resets_at: string | null;
  retry_after: string | null;
  is_complete: boolean;
  is_error: boolean;
};

type FullSyncSnapshot = {
  activitiesSeen: number;
  activitiesProcessed: number;
  isComplete: boolean;
  updatedAt: string | null;
};

type StoredConnection = {
  userId: string;
  displayName: string | null;
  stravaAthleteId: string | null;
  pendingSyncTaskId?: string | null;
  pendingSyncStartedAt?: number | null;
  pendingSyncMode?: ManualSyncMode | null;
  lastFullSyncSnapshot?: FullSyncSnapshot | null;
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

const formatActivityLinkDate = (value: string) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "2-digit",
  }).format(new Date(value));

const formatDurationLabel = (totalSeconds: number) => {
  if (totalSeconds <= 45) {
    return "under 1 min";
  }

  const roundedMinutes = Math.max(1, Math.ceil(totalSeconds / 60));
  if (roundedMinutes < 60) {
    return `${roundedMinutes} min${roundedMinutes === 1 ? "" : "s"}`;
  }

  const hours = Math.floor(roundedMinutes / 60);
  const minutes = roundedMinutes % 60;
  if (minutes === 0) {
    return `${hours} hr${hours === 1 ? "" : "s"}`;
  }

  return `${hours} hr${hours === 1 ? "" : "s"} ${minutes} min${minutes === 1 ? "" : "s"}`;
};

const formatCountLabel = (value: number) => value.toLocaleString("en-GB");

const parseIsoTimestamp = (value: string | null) => {
  if (!value) {
    return null;
  }

  const parsedTime = Date.parse(value);
  return Number.isNaN(parsedTime) ? null : parsedTime;
};

const deriveLiveCountdownSeconds = (value: string | null) => {
  const parsedTime = parseIsoTimestamp(value);
  if (parsedTime === null) {
    return null;
  }

  const secondsRemaining = Math.ceil((parsedTime - Date.now()) / 1000);
  return secondsRemaining > 0 ? secondsRemaining : 0;
};

const isStravaTotalCountStale = (
  totalStravaActivities: number | null,
  updatedAt: string | null
) => {
  if (totalStravaActivities === null) {
    return true;
  }

  const refreshedAt = parseIsoTimestamp(updatedAt);
  if (refreshedAt === null) {
    return true;
  }

  return Date.now() - refreshedAt > STRAVA_TOTAL_STALE_AFTER_MS;
};

const captureFullSyncSnapshot = (status: StravaSyncTaskStatus): FullSyncSnapshot => ({
  activitiesSeen: status.activities_seen,
  activitiesProcessed: status.activities_processed,
  isComplete: status.is_complete && !status.is_error,
  updatedAt: status.updated_at,
});

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
    return {
      ...parsed,
      pendingSyncTaskId: parsed.pendingSyncTaskId ?? null,
      pendingSyncStartedAt:
        typeof parsed.pendingSyncStartedAt === "number"
          ? parsed.pendingSyncStartedAt
          : parsed.pendingSyncTaskId
            ? Date.now()
            : null,
      pendingSyncMode:
        parsed.pendingSyncMode === "full" || parsed.pendingSyncMode === "latest"
          ? parsed.pendingSyncMode
          : parsed.pendingSyncTaskId
            ? "latest"
            : null,
      lastFullSyncSnapshot:
        parsed.lastFullSyncSnapshot &&
        typeof parsed.lastFullSyncSnapshot.activitiesSeen === "number" &&
        typeof parsed.lastFullSyncSnapshot.activitiesProcessed === "number"
          ? {
              activitiesSeen: parsed.lastFullSyncSnapshot.activitiesSeen,
              activitiesProcessed: parsed.lastFullSyncSnapshot.activitiesProcessed,
              isComplete: Boolean(parsed.lastFullSyncSnapshot.isComplete),
              updatedAt:
                typeof parsed.lastFullSyncSnapshot.updatedAt === "string"
                  ? parsed.lastFullSyncSnapshot.updatedAt
                  : null,
            }
          : null,
    };
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

const createPendingSyncStatus = (
  taskId: string,
  syncMode: Exclude<SyncMode, "activity"> = "latest"
): StravaSyncTaskStatus => ({
  task_id: taskId,
  state: "PENDING",
  message: "Waiting for the Strava sync to start.",
  sync_mode: syncMode,
  activities_seen: 0,
  activities_processed: 0,
  activities_with_matches: 0,
  activities_skipped_no_polyline: 0,
  activities_skipped_invalid_polyline: 0,
  activities_skipped_unverified_elevation: 0,
  bag_rows_written: 0,
  cached_strava_activities: null,
  total_strava_activities: null,
  progress_percentage: 0,
  progress_is_indeterminate: syncMode === "full" || syncMode === "totals",
  sync_phase: "pending",
  started_at: null,
  updated_at: null,
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

const mergePolledTaskStatus = (
  payload: StravaSyncTaskStatus,
  previousStatus: StravaSyncTaskStatus | null,
  fallbackSyncMode: Exclude<SyncMode, "activity"> | "activity"
): StravaSyncTaskStatus => {
  const resolvedSyncMode = payload.sync_mode ?? previousStatus?.sync_mode ?? fallbackSyncMode;
  const shouldPreserveRetryCounts = payload.state === "RETRY";
  const preserveRetryNumber = (
    nextValue: number,
    previousValue: number | undefined
  ) => (shouldPreserveRetryCounts && nextValue === 0 ? previousValue ?? 0 : nextValue);
  const preserveOptionalNumber = (
    nextValue: number | null,
    previousValue: number | null | undefined
  ) => (nextValue ?? previousValue ?? null);
  const preserveOptionalText = (
    nextValue: string | null,
    previousValue: string | null | undefined
  ) => nextValue ?? previousValue ?? null;

  return {
    ...payload,
    sync_mode: resolvedSyncMode,
    activities_seen: preserveRetryNumber(payload.activities_seen, previousStatus?.activities_seen),
    activities_processed: preserveRetryNumber(
      payload.activities_processed,
      previousStatus?.activities_processed
    ),
    activities_with_matches: preserveRetryNumber(
      payload.activities_with_matches,
      previousStatus?.activities_with_matches
    ),
    activities_skipped_no_polyline: preserveRetryNumber(
      payload.activities_skipped_no_polyline,
      previousStatus?.activities_skipped_no_polyline
    ),
    activities_skipped_invalid_polyline: preserveRetryNumber(
      payload.activities_skipped_invalid_polyline,
      previousStatus?.activities_skipped_invalid_polyline
    ),
    activities_skipped_unverified_elevation: preserveRetryNumber(
      payload.activities_skipped_unverified_elevation,
      previousStatus?.activities_skipped_unverified_elevation
    ),
    bag_rows_written: preserveRetryNumber(
      payload.bag_rows_written,
      previousStatus?.bag_rows_written
    ),
    cached_strava_activities: preserveOptionalNumber(
      payload.cached_strava_activities,
      previousStatus?.cached_strava_activities
    ),
    total_strava_activities: preserveOptionalNumber(
      payload.total_strava_activities,
      previousStatus?.total_strava_activities
    ),
    rate_limit_scope: payload.rate_limit_scope ?? previousStatus?.rate_limit_scope ?? null,
    read_window_limit: preserveOptionalNumber(
      payload.read_window_limit,
      previousStatus?.read_window_limit
    ),
    read_window_usage: preserveOptionalNumber(
      payload.read_window_usage,
      previousStatus?.read_window_usage
    ),
    read_window_remaining: preserveOptionalNumber(
      payload.read_window_remaining,
      previousStatus?.read_window_remaining
    ),
    read_daily_limit: preserveOptionalNumber(
      payload.read_daily_limit,
      previousStatus?.read_daily_limit
    ),
    read_daily_usage: preserveOptionalNumber(
      payload.read_daily_usage,
      previousStatus?.read_daily_usage
    ),
    read_daily_remaining: preserveOptionalNumber(
      payload.read_daily_remaining,
      previousStatus?.read_daily_remaining
    ),
    read_window_resets_at: preserveOptionalText(
      payload.read_window_resets_at,
      previousStatus?.read_window_resets_at
    ),
    read_daily_resets_at: preserveOptionalText(
      payload.read_daily_resets_at,
      previousStatus?.read_daily_resets_at
    ),
    progress_is_indeterminate:
      payload.progress_is_indeterminate ||
      ((payload.state === "PENDING" || payload.state === "RETRY") &&
        (resolvedSyncMode === "full" || resolvedSyncMode === "totals")),
  };
};

const attachPendingSync = (
  connection: StoredConnection,
  taskId: string,
  pendingSyncStartedAt = Date.now(),
  pendingSyncMode: ManualSyncMode = "latest"
): StoredConnection => ({
  ...connection,
  pendingSyncTaskId: taskId,
  pendingSyncStartedAt,
  pendingSyncMode,
});

const clearPendingSync = (connection: StoredConnection | null): StoredConnection | null => {
  if (connection === null) {
    return null;
  }

  return {
    userId: connection.userId,
    displayName: connection.displayName,
    stravaAthleteId: connection.stravaAthleteId,
    lastFullSyncSnapshot: connection.lastFullSyncSnapshot ?? null,
  };
};

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
  const initialConnection = readStoredConnection();
  const [munros, setMunros] = useState<Munro[]>([]);
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [connection, setConnection] = useState<StoredConnection | null>(initialConnection);
  const [syncTaskId, setSyncTaskId] = useState<string | null>(
    initialConnection?.pendingSyncTaskId ?? null
  );
  const [syncStatus, setSyncStatus] = useState<StravaSyncTaskStatus | null>(
    initialConnection?.pendingSyncTaskId
      ? createPendingSyncStatus(
          initialConnection.pendingSyncTaskId,
          initialConnection.pendingSyncMode ?? "latest"
        )
      : null
  );
  const syncStatusRef = useRef<StravaSyncTaskStatus | null>(syncStatus);
  const [totalsTaskId, setTotalsTaskId] = useState<string | null>(null);
  const [totalsStatus, setTotalsStatus] = useState<StravaSyncTaskStatus | null>(null);
  const totalsStatusRef = useRef<StravaSyncTaskStatus | null>(totalsStatus);
  const autoTotalsRefreshKeyRef = useRef<string | null>(null);
  const [stravaOAuthStatus, setStravaOAuthStatus] = useState<StravaOAuthStatus | null>(null);
  const [showStravaSetup, setShowStravaSetup] = useState(false);
  const [stravaClientId, setStravaClientId] = useState("");
  const [stravaClientSecret, setStravaClientSecret] = useState("");
  const [savingStravaSetup, setSavingStravaSetup] = useState(false);
  const [stravaSetupError, setStravaSetupError] = useState<string | null>(null);
  const [connectionReady, setConnectionReady] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"success" | "error" | "info">("success");
  const [loadingMunros, setLoadingMunros] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [mapType, setMapType] = useState<BasemapMode>(hasOsBasemap ? "os" : "topo");
  const [mapMunroFilter, setMapMunroFilter] = useState<MapMunroFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "bagged" | "remaining">("all");
  const [sortBy, setSortBy] = useState<"height" | "alphabetical">("height");
  const [selectedMunro, setSelectedMunro] = useState<Munro | null>(null);

  const updateConnection = (nextConnection: StoredConnection | null) => {
    persistConnection(nextConnection);
    setConnection(nextConnection);
  };

  const beginPendingSync = (taskId: string, syncMode: ManualSyncMode) => {
    const pendingSyncStartedAt = Date.now();
    setSyncTaskId(taskId);
    setSyncStatus(createPendingSyncStatus(taskId, syncMode));
    setConnection((current) => {
      if (!current) {
        return current;
      }
      const nextConnection = attachPendingSync(
        current,
        taskId,
        pendingSyncStartedAt,
        syncMode
      );
      persistConnection(nextConnection);
      return nextConnection;
    });
  };

  const beginTotalsRefresh = (taskId: string) => {
    setTotalsTaskId(taskId);
    setTotalsStatus(createPendingSyncStatus(taskId, "totals"));
  };

  const clearPendingSyncState = () => {
    setSyncTaskId(null);
    setSyncStatus(null);
    setConnection((current) => {
      const nextConnection = clearPendingSync(current);
      persistConnection(nextConnection);
      return nextConnection;
    });
  };

  const clearTotalsRefreshState = () => {
    setTotalsTaskId(null);
    setTotalsStatus(null);
  };

  const persistFullSyncSnapshot = (status: StravaSyncTaskStatus) => {
    setConnection((current) => {
      if (!current) {
        return current;
      }

      const nextConnection = {
        ...current,
        lastFullSyncSnapshot: captureFullSyncSnapshot(status),
      };
      persistConnection(nextConnection);
      return nextConnection;
    });
  };

  useEffect(() => {
    syncStatusRef.current = syncStatus;
  }, [syncStatus]);

  useEffect(() => {
    totalsStatusRef.current = totalsStatus;
  }, [totalsStatus]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");

    if (status === "connected" && params.get("user_id")) {
      const nextSyncTaskId = params.get("sync_task_id");
      const syncEnqueued = params.get("sync_enqueued") === "true";
      const baseConnection: StoredConnection = {
        userId: params.get("user_id") ?? "",
        displayName: params.get("display_name"),
        stravaAthleteId: params.get("strava_athlete_id"),
      };
      const nextConnection =
        syncEnqueued && nextSyncTaskId
          ? attachPendingSync(baseConnection, nextSyncTaskId, Date.now(), "latest")
          : clearPendingSync(baseConnection);
      updateConnection(nextConnection);

      setSyncTaskId(syncEnqueued ? nextSyncTaskId : null);
      setSyncStatus(
        syncEnqueued && nextSyncTaskId ? createPendingSyncStatus(nextSyncTaskId, "latest") : null
      );
      setStatusTone(syncEnqueued ? "info" : "success");
      setStatusMessage(
        syncEnqueued ? null : "Strava connected. You can trigger a sync again at any time."
      );
    } else if (status === "error") {
      setSyncTaskId(null);
      setSyncStatus(null);
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
  }, [connection?.userId, connectionReady, refreshKey]);

  useEffect(() => {
    if (!connectionReady) {
      return;
    }

    if (connection) {
      setStravaOAuthStatus(null);
      setShowStravaSetup(false);
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
        if (!payload.oauth_available) {
          setShowStravaSetup(true);
        }
      } catch {
        if (!controller.signal.aborted) {
          setStravaOAuthStatus(null);
        }
      }
    };

    void fetchStravaStatus();
    return () => controller.abort();
  }, [connection?.userId, connectionReady, refreshKey]);

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
  }, [connection?.userId, connectionReady, refreshKey]);

  useEffect(() => {
    if (
      !connectionReady ||
      !connection?.userId ||
      !dashboard ||
      loadingDashboard ||
      syncTaskId !== null ||
      totalsTaskId !== null
    ) {
      return;
    }

    if (
      !isStravaTotalCountStale(
        dashboard.total_strava_activities,
        dashboard.total_strava_activities_updated_at
      )
    ) {
      return;
    }

    const refreshAttemptKey = [
      connection.userId,
      dashboard.total_strava_activities ?? "missing",
      dashboard.total_strava_activities_updated_at ?? "stale",
    ].join(":");
    if (autoTotalsRefreshKeyRef.current === refreshAttemptKey) {
      return;
    }

    autoTotalsRefreshKeyRef.current = refreshAttemptKey;

    const queueTotalsRefresh = async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/strava/users/${connection.userId}/totals/refresh`,
          {
            method: "POST",
          }
        );

        if (!response.ok) {
          throw new Error("Unable to refresh Strava totals right now.");
        }

        const responsePayload = (await response.json()) as {
          task_id: string;
        };
        beginTotalsRefresh(responsePayload.task_id);
      } catch {
        clearTotalsRefreshState();
      }
    };

    void queueTotalsRefresh();
  }, [
    connection?.userId,
    connectionReady,
    dashboard,
    loadingDashboard,
    syncTaskId,
    totalsTaskId,
  ]);

  useEffect(() => {
    if (!connectionReady || !syncTaskId) {
      return;
    }

    const controller = new AbortController();
    let timeoutId: number | null = null;
    const pendingSyncStartedAt = connection?.pendingSyncStartedAt ?? null;
    const pendingSyncMode = connection?.pendingSyncMode ?? "latest";

    const pollTaskStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/strava/tasks/${syncTaskId}`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Unable to check the Strava sync progress.");
        }

        const payload = (await response.json()) as StravaSyncTaskStatus;
        if (controller.signal.aborted) {
          return;
        }

        const resolvedPayload = mergePolledTaskStatus(
          payload,
          syncStatusRef.current,
          connection?.pendingSyncMode ?? "latest"
        );

        if (resolvedPayload.sync_mode === "full") {
          persistFullSyncSnapshot(resolvedPayload);
        }

        if (
          resolvedPayload.state === "PENDING" &&
          pendingSyncStartedAt !== null &&
          Date.now() - pendingSyncStartedAt >= PENDING_SYNC_TIMEOUT_MS
        ) {
          clearPendingSyncState();
          setStatusTone("error");
          setStatusMessage(
            pendingSyncMode === "full"
              ? "The Strava sync did not start in time. Try syncing all activities again."
              : "The Strava sync did not start in time. Try syncing the latest activities again."
          );
          return;
        }

        setSyncStatus(resolvedPayload);

        if (resolvedPayload.is_complete) {
          clearPendingSyncState();

          if (resolvedPayload.is_error) {
            setStatusTone("error");
            setStatusMessage(resolvedPayload.message);
          } else {
            setStatusTone("success");
            setStatusMessage(resolvedPayload.message);
            setRefreshKey((current) => current + 1);
          }
          return;
        }

        timeoutId = window.setTimeout(pollTaskStatus, 1500);
      } catch {
        if (controller.signal.aborted) {
          return;
        }

        timeoutId = window.setTimeout(pollTaskStatus, 2500);
      }
    };

    void pollTaskStatus();

    return () => {
      controller.abort();
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [connection?.pendingSyncMode, connection?.pendingSyncStartedAt, connectionReady, syncTaskId]);

  useEffect(() => {
    if (!connectionReady || !totalsTaskId) {
      return;
    }

    const controller = new AbortController();
    let timeoutId: number | null = null;

    const pollTotalsStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/strava/tasks/${totalsTaskId}`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Unable to check the Strava totals refresh progress.");
        }

        const payload = (await response.json()) as StravaSyncTaskStatus;
        if (controller.signal.aborted) {
          return;
        }

        const resolvedPayload = mergePolledTaskStatus(
          payload,
          totalsStatusRef.current,
          "totals"
        );
        setTotalsStatus(resolvedPayload);

        if (resolvedPayload.is_complete) {
          clearTotalsRefreshState();
          if (!resolvedPayload.is_error) {
            setRefreshKey((current) => current + 1);
          }
          return;
        }

        timeoutId = window.setTimeout(pollTotalsStatus, 1500);
      } catch {
        if (controller.signal.aborted) {
          return;
        }

        timeoutId = window.setTimeout(pollTotalsStatus, 2500);
      }
    };

    void pollTotalsStatus();

    return () => {
      controller.abort();
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [connectionReady, totalsTaskId]);

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

  const displayedMapMunros = useMemo(() => {
    return munros.filter((munro) => {
      if (mapMunroFilter === "bagged") {
        return munro.is_bagged;
      }
      if (mapMunroFilter === "unbagged") {
        return !munro.is_bagged;
      }
      return true;
    });
  }, [mapMunroFilter, munros]);

  const summary = useMemo(() => {
    return {
      totalMunros: dashboard?.total_munros ?? munros.length,
      baggedMunros: dashboard?.bagged_munros ?? 0,
      completionPercentage: dashboard?.completion_percentage ?? 0,
      cachedStravaActivities: dashboard?.cached_strava_activities ?? 0,
      totalStravaActivities: dashboard?.total_strava_activities ?? null,
      totalStravaActivitiesUpdatedAt: dashboard?.total_strava_activities_updated_at ?? null,
      totalAscentMetres: dashboard?.total_ascent_metres ?? 0,
      lastBaggedAt: dashboard?.last_bagged_at ?? null,
    };
  }, [dashboard, munros.length]);

  const activeSyncStatus = syncTaskId
    ? syncStatus ??
      createPendingSyncStatus(syncTaskId, connection?.pendingSyncMode ?? "latest")
    : null;
  const activeTotalsStatus = totalsTaskId
    ? totalsStatus ?? createPendingSyncStatus(totalsTaskId, "totals")
    : null;
  const visibleTotalsStatus = syncTaskId === null ? activeTotalsStatus : null;
  const activeSyncMode = activeSyncStatus?.sync_mode ?? connection?.pendingSyncMode ?? null;
  const isFullHistorySync = activeSyncMode === "full";
  const fullSyncCachedActivities =
    activeSyncStatus?.cached_strava_activities ?? summary.cachedStravaActivities;
  const fullSyncTotalActivities =
    activeSyncStatus?.total_strava_activities ?? summary.totalStravaActivities;
  const hasKnownFullSyncTotal =
    isFullHistorySync &&
    fullSyncTotalActivities !== null &&
    fullSyncTotalActivities > 0 &&
    fullSyncCachedActivities !== null;
  const knownDownloadedActivities = activeSyncStatus
    ? Math.max(activeSyncStatus.activities_seen, activeSyncStatus.activities_processed)
    : 0;
  const knownProgressPercentage = activeSyncStatus?.progress_percentage ?? 0;
  const shortWindowResetSeconds = deriveLiveCountdownSeconds(
    activeSyncStatus?.read_window_resets_at ?? activeSyncStatus?.retry_after ?? null
  );
  const dailyResetSeconds = deriveLiveCountdownSeconds(
    activeSyncStatus?.read_daily_resets_at ?? activeSyncStatus?.retry_after ?? null
  );
  const totalsShortWindowResetSeconds = deriveLiveCountdownSeconds(
    visibleTotalsStatus?.read_window_resets_at ?? visibleTotalsStatus?.retry_after ?? null
  );
  const totalsDailyResetSeconds = deriveLiveCountdownSeconds(
    visibleTotalsStatus?.read_daily_resets_at ?? visibleTotalsStatus?.retry_after ?? null
  );
  const liveRateLimitWaitSeconds = (() => {
    if (!activeSyncStatus) {
      return null;
    }

    if (activeSyncStatus.sync_phase === "waiting_for_rate_limit") {
      if (activeSyncStatus.rate_limit_scope === "daily") {
        return dailyResetSeconds ?? activeSyncStatus.rate_limit_wait_seconds ?? null;
      }
      return shortWindowResetSeconds ?? activeSyncStatus.rate_limit_wait_seconds ?? null;
    }

    if (!activeSyncStatus.retry_after) {
      return activeSyncStatus?.rate_limit_wait_seconds ?? null;
    }

    const retryAt = parseIsoTimestamp(activeSyncStatus.retry_after);
    if (retryAt === null) {
      return activeSyncStatus.rate_limit_wait_seconds ?? null;
    }

    const derivedWaitSeconds = Math.ceil((retryAt - Date.now()) / 1000);
    if (derivedWaitSeconds <= 0) {
      return activeSyncStatus.rate_limit_wait_seconds ?? null;
    }

    return derivedWaitSeconds;
  })();
  const liveRemainingSeconds = (() => {
    if (!activeSyncStatus) {
      return null;
    }

    const estimatedRemainingSeconds = activeSyncStatus.estimated_remaining_seconds;
    if (
      liveRateLimitWaitSeconds !== null &&
      activeSyncStatus.rate_limit_wait_seconds !== null &&
      estimatedRemainingSeconds !== null
    ) {
      return Math.max(
        liveRateLimitWaitSeconds,
        liveRateLimitWaitSeconds +
          Math.max(estimatedRemainingSeconds - activeSyncStatus.rate_limit_wait_seconds, 0)
      );
    }

    if (liveRateLimitWaitSeconds !== null) {
      return liveRateLimitWaitSeconds;
    }

    return estimatedRemainingSeconds;
  })();
  const syncProgressPhases = activeSyncStatus
    ? (() => {
        const syncedSoFarLabel =
          hasKnownFullSyncTotal && fullSyncCachedActivities !== null && fullSyncTotalActivities !== null
            ? `Synced ${formatCountLabel(fullSyncCachedActivities)} of ${formatCountLabel(fullSyncTotalActivities)} activities so far`
            : null;
        const downloadedThisRunLabel =
          knownDownloadedActivities > 0
            ? `Downloaded ${formatCountLabel(knownDownloadedActivities)} activities in this pass`
            : null;
        const processingThisRunLabel =
          knownDownloadedActivities > 0
            ? `Processing ${formatCountLabel(activeSyncStatus.activities_processed)} of ${formatCountLabel(knownDownloadedActivities)} downloaded activities`
            : null;

        if (activeSyncStatus.sync_phase === "waiting_for_rate_limit") {
          const waitPrimary =
            activeSyncStatus.rate_limit_scope === "daily"
              ? dailyResetSeconds !== null
                ? `Daily Strava read limit reached. Resets at midnight UTC in ${formatDurationLabel(dailyResetSeconds)}.`
                : "Daily Strava read limit reached. Waiting for the next reset."
              : shortWindowResetSeconds !== null
                ? `15-minute Strava read limit reached. Resets in ${formatDurationLabel(shortWindowResetSeconds)}.`
                : "15-minute Strava read limit reached. Waiting for the next reset.";

          return {
            primary: waitPrimary,
            details: [
              syncedSoFarLabel,
              downloadedThisRunLabel,
              processingThisRunLabel,
            ].filter(Boolean) as string[],
          };
        }

        if (
          activeSyncStatus.sync_mode === "totals" ||
          activeSyncStatus.sync_phase === "discovering"
        ) {
          return {
            primary: "Counting your Strava history.",
            details: [
              activeSyncStatus.total_strava_activities !== null
                ? `Counted ${formatCountLabel(activeSyncStatus.total_strava_activities)} activities so far`
                : "Working through your Strava activity pages",
            ],
          };
        }

        if (activeSyncStatus.sync_mode === "full") {
          return {
            primary: "Syncing your full Strava history.",
            details: [
              syncedSoFarLabel,
              downloadedThisRunLabel ?? "Downloading older Strava activities",
              processingThisRunLabel,
            ].filter(Boolean) as string[],
          };
        }

        if (activeSyncStatus.sync_mode === "latest") {
          return {
            primary: "Syncing your latest Strava activities.",
            details: [
              knownDownloadedActivities > 0
                ? `Downloading ${formatCountLabel(knownDownloadedActivities)} activities`
                : "Downloading your latest activities",
              knownDownloadedActivities > 0
                ? `Processing ${formatCountLabel(activeSyncStatus.activities_processed)} of ${formatCountLabel(knownDownloadedActivities)} downloaded activities`
                : "Waiting for Strava to return your latest list",
            ],
          };
        }

        return {
          primary: "Processing your Strava activity",
          details: [],
        };
      })()
    : null;
  const syncTimeRemainingLabel =
    activeSyncStatus && liveRemainingSeconds !== null
      ? `${activeSyncStatus.sync_phase === "waiting_for_rate_limit" ? "At least" : "About"} ${formatDurationLabel(liveRemainingSeconds)} remaining`
      : null;
  const activeQuotaLabels = activeSyncStatus
    ? [
        activeSyncStatus.read_window_limit !== null &&
        activeSyncStatus.read_window_remaining !== null
          ? `Reads left this window: ${formatCountLabel(activeSyncStatus.read_window_remaining)} / ${formatCountLabel(activeSyncStatus.read_window_limit)}`
          : null,
        activeSyncStatus.read_daily_limit !== null &&
        activeSyncStatus.read_daily_usage !== null
          ? `Today: ${formatCountLabel(activeSyncStatus.read_daily_usage)} / ${formatCountLabel(activeSyncStatus.read_daily_limit)} used`
          : null,
      ].filter(Boolean)
    : [];
  const fullSyncProgressCountLabel = (() => {
    const cachedActivityCount =
      activeSyncStatus?.sync_mode === "full" && activeSyncStatus.cached_strava_activities !== null
        ? activeSyncStatus.cached_strava_activities
        : summary.cachedStravaActivities;
    const totalActivityCount =
      activeSyncStatus?.sync_mode === "full" && activeSyncStatus.total_strava_activities !== null
        ? activeSyncStatus.total_strava_activities
        : visibleTotalsStatus?.total_strava_activities ?? summary.totalStravaActivities;

    if (
      cachedActivityCount !== null &&
      totalActivityCount !== null &&
      totalActivityCount > 0
    ) {
      return `${formatCountLabel(cachedActivityCount)} / ${formatCountLabel(totalActivityCount)} synced`;
    }

    const lastFullSyncSnapshot = connection?.lastFullSyncSnapshot;
    if (lastFullSyncSnapshot && lastFullSyncSnapshot.activitiesSeen > 0) {
      const syncedActivities = lastFullSyncSnapshot.isComplete
        ? lastFullSyncSnapshot.activitiesSeen
        : lastFullSyncSnapshot.activitiesProcessed;
      const suffix = lastFullSyncSnapshot.isComplete ? "synced" : "synced so far";
      return `${formatCountLabel(syncedActivities)} of ${formatCountLabel(lastFullSyncSnapshot.activitiesSeen)} ${suffix}`;
    }

    if (summary.cachedStravaActivities > 0) {
      return `${formatCountLabel(summary.cachedStravaActivities)} cached so far`;
    }

    return "No activities cached yet";
  })();
  const listTitle =
    filter === "bagged" ? "Bagged Munros" : filter === "remaining" ? "Remaining Munros" : "All Munros";
  const listSummaryLabel = `${displayedMunros.length.toLocaleString("en-GB")} shown`;
  const syncBadgeLabel = activeSyncStatus
    ? activeSyncStatus.sync_phase === "waiting_for_rate_limit"
      ? "Waiting"
      : "Syncing"
    : visibleTotalsStatus
      ? visibleTotalsStatus.sync_phase === "waiting_for_rate_limit"
        ? "Waiting"
        : "Refreshing"
      : connection
        ? "Ready"
        : "Offline";
  const syncBadgeClass = activeSyncStatus
    ? activeSyncStatus.sync_phase === "waiting_for_rate_limit"
      ? "bg-sky-100 text-sky-800"
      : "bg-emerald-100 text-emerald-900"
    : visibleTotalsStatus
      ? visibleTotalsStatus.sync_phase === "waiting_for_rate_limit"
        ? "bg-sky-100 text-sky-800"
        : "bg-stone-200 text-stone-700"
      : connection
        ? "bg-emerald-100 text-emerald-900"
        : "bg-stone-200 text-stone-600";

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

  const submitManualSync = async (syncMode: ManualSyncMode) => {
    if (!connection?.userId || syncTaskId) {
      return;
    }

    try {
      const searchParams = new URLSearchParams({ mode: syncMode });
      const response = await fetch(
        `${API_BASE_URL}/strava/users/${connection.userId}/sync?${searchParams.toString()}`,
        {
          method: "POST",
        }
      );

      if (!response.ok) {
        const errorPayload = (await response.json().catch(() => ({
          detail: "Unable to queue a Strava sync right now.",
        }))) as { detail?: string };
        throw new Error(errorPayload.detail || "Unable to queue a Strava sync right now.");
      }

      const responsePayload = (await response.json()) as {
        task_id: string;
        sync_mode: ManualSyncMode;
      };
      beginPendingSync(responsePayload.task_id, responsePayload.sync_mode ?? syncMode);
      setStatusTone("info");
      setStatusMessage(null);
    } catch (requestError) {
      setStatusTone("error");
      setStatusMessage(
        requestError instanceof Error
          ? requestError.message
          : "Unable to queue a Strava sync right now."
      );
    }
  };

  const submitStravaSettings = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setStravaSetupError(null);

    const normalisedClientId = Number.parseInt(stravaClientId.trim(), 10);
    if (!Number.isFinite(normalisedClientId) || normalisedClientId <= 0) {
      setStravaSetupError("Enter a valid Strava app ID.");
      return;
    }

    if (!stravaClientSecret.trim()) {
      setStravaSetupError("Enter a Strava client secret.");
      return;
    }

    const payload: StravaOAuthSettingsInput = {
      client_id: normalisedClientId,
      client_secret: stravaClientSecret.trim(),
    };

    try {
      setSavingStravaSetup(true);
      const response = await fetch(`${API_BASE_URL}/strava/oauth/settings`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      const responsePayload = response.ok
        ? ((await response.json()) as StravaOAuthStatus)
        : await response.json().catch(() => ({ detail: "Unable to save Strava app credentials." }));

      if (!response.ok) {
        throw new Error(responsePayload.detail || "Unable to save Strava app credentials.");
      }

      setStravaOAuthStatus(responsePayload);
      setStatusTone("success");
      setStatusMessage("Strava app credentials saved. You can now connect Strava.");
      setShowStravaSetup(false);
      setStravaClientSecret("");
    } catch (requestError) {
      setStravaSetupError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to save Strava app credentials."
      );
    } finally {
      setSavingStravaSetup(false);
    }
  };

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
        className={`fixed inset-y-0 left-0 z-[1002] w-96 transform overflow-y-auto border-r border-stone-200/70 bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.96),_rgba(255,250,241,0.98)_55%,_rgba(244,239,227,0.99)_100%)] px-4 py-5 shadow-2xl transition-transform duration-300 ease-in-out custom-scrollbar sm:px-5 lg:static lg:translate-x-0 lg:px-6 ${isSidebarOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex min-h-full flex-col gap-5 pb-6">
          <section className="rounded-[32px] border border-stone-200/80 bg-white/85 p-5 shadow-[0_24px_60px_-34px_rgba(15,23,42,0.55)] backdrop-blur">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.3em] text-emerald-800/70">
                  Scottish hill bagging
                </p>
                <h1 className="mt-1 text-3xl font-black tracking-tight text-emerald-950">
                  MunroStream
                </h1>
                <p className="mt-2 max-w-xs text-sm leading-6 text-stone-600">
                  Track your Strava history, monitor sync progress, and keep your next summit in view.
                </p>
              </div>
              <div className="flex items-start gap-3">
                <span
                  className={`inline-flex rounded-full px-3 py-1 text-[10px] font-black uppercase tracking-[0.22em] ${syncBadgeClass}`}
                >
                  {syncBadgeLabel}
                </span>
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
            </div>

            <div className="mt-5 rounded-[28px] bg-gradient-to-br from-emerald-950 via-emerald-900 to-lime-800 p-5 text-white shadow-xl">
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

            {activeSyncStatus ? (
              <div
                data-testid="sync-progress"
                className="mt-4 rounded-[26px] border border-sky-200 bg-sky-50/95 px-4 py-4 text-sky-950 shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-[11px] font-black uppercase tracking-[0.25em] text-sky-700/80">
                      {isFullHistorySync ? "Strava history sync" : "Strava sync"}
                    </p>
                    <p className="mt-1 text-sm font-semibold leading-6">
                      {syncProgressPhases?.primary ?? activeSyncStatus.message}
                    </p>
                  </div>
                  <p className="shrink-0 text-sm font-black">
                    {activeSyncStatus.sync_phase === "waiting_for_rate_limit"
                      ? "Waiting"
                      : activeSyncStatus.sync_phase === "discovering"
                        ? "Counting"
                        : activeSyncStatus.progress_is_indeterminate
                        ? `${Math.round(knownProgressPercentage)}%`
                        : `${Math.round(activeSyncStatus.progress_percentage)}%`}
                  </p>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-sky-100">
                  <div
                    className={`h-full rounded-full bg-sky-600 transition-all duration-700 ${
                      activeSyncStatus.progress_is_indeterminate ? "animate-pulse" : ""
                    }`}
                    style={{
                      width: `${Math.max(
                        activeSyncStatus.progress_is_indeterminate ? 12 : 8,
                        activeSyncStatus.progress_percentage
                      )}%`,
                    }}
                  />
                </div>
                <div className="mt-3 space-y-1.5 text-xs text-sky-800">
                  {(syncProgressPhases?.details ?? []).map((detail) => (
                    <p key={detail}>{detail}</p>
                  ))}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    {syncTimeRemainingLabel ? <span>{syncTimeRemainingLabel}</span> : null}
                    {activeSyncStatus.activities_with_matches > 0 ? (
                      <span>
                        {activeSyncStatus.activities_with_matches} Munro
                        {activeSyncStatus.activities_with_matches === 1 ? " match" : " matches"}
                      </span>
                    ) : null}
                  </div>
                  {activeQuotaLabels.length > 0 ? (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      {activeQuotaLabels.map((label) => (
                        <span key={label}>{label}</span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {visibleTotalsStatus ? (
              <div
                data-testid="totals-refresh-status"
                className="mt-4 rounded-[22px] border border-stone-200 bg-stone-50/95 px-4 py-3 text-sm text-stone-800 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[11px] font-black uppercase tracking-[0.22em] text-stone-500">
                      Strava totals
                    </p>
                    <p className="mt-1 font-semibold text-stone-900">
                      {visibleTotalsStatus.sync_phase === "waiting_for_rate_limit"
                        ? visibleTotalsStatus.rate_limit_scope === "daily"
                          ? `Refreshing Strava totals pauses until midnight UTC in ${formatDurationLabel(
                              totalsDailyResetSeconds ?? visibleTotalsStatus.rate_limit_wait_seconds ?? 0
                            )}.`
                          : `Refreshing Strava totals pauses until the next 15-minute window in ${formatDurationLabel(
                              totalsShortWindowResetSeconds ?? visibleTotalsStatus.rate_limit_wait_seconds ?? 0
                            )}.`
                        : "Refreshing Strava totals..."}
                    </p>
                  </div>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-stone-600">
                    {visibleTotalsStatus.sync_phase === "waiting_for_rate_limit"
                      ? "Waiting"
                      : "Counting"}
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-xs text-stone-600">
                  <p>
                    {visibleTotalsStatus.total_strava_activities !== null
                      ? `Counted ${formatCountLabel(visibleTotalsStatus.total_strava_activities)} activities so far`
                      : "Checking how many Strava activities are available in total"}
                  </p>
                  {visibleTotalsStatus.read_window_limit !== null &&
                  visibleTotalsStatus.read_window_remaining !== null ? (
                    <p>
                      Reads left this window:{" "}
                      {formatCountLabel(visibleTotalsStatus.read_window_remaining)} /{" "}
                      {formatCountLabel(visibleTotalsStatus.read_window_limit)}
                    </p>
                  ) : null}
                </div>
              </div>
            ) : null}

            {statusMessage ? (
              <div
                data-testid="connection-status"
                className={`mt-4 rounded-[22px] px-4 py-3 text-sm leading-6 ${
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

            <div className="mt-5 border-t border-stone-200/80 pt-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] font-black uppercase tracking-[0.25em] text-stone-500">
                    Strava
                  </p>
                  <p className="mt-2 text-sm leading-6 text-stone-700">
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
                      updateConnection(null);
                      setSyncTaskId(null);
                      setSyncStatus(null);
                      clearTotalsRefreshState();
                      setStatusTone("info");
                      setStatusMessage("Local Strava session cleared from this browser.");
                    }}
                    className="rounded-xl border border-stone-200 px-3 py-2 text-xs font-black uppercase tracking-[0.2em] text-stone-600 transition hover:border-stone-300 hover:text-stone-900"
                  >
                    Clear
                  </button>
                ) : null}
              </div>

              {connection ? (
                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    type="button"
                    data-testid="sync-latest-activities"
                    onClick={() => {
                      void submitManualSync("latest");
                    }}
                    disabled={syncTaskId !== null}
                    className="rounded-xl bg-emerald-800 px-4 py-3 text-sm font-black text-white transition hover:bg-emerald-900 disabled:cursor-not-allowed disabled:bg-emerald-400"
                  >
                    {syncTaskId && !isFullHistorySync
                      ? "Syncing latest activities..."
                      : "Sync latest activities"}
                  </button>
                  <button
                    type="button"
                    data-testid="sync-all-activities"
                    onClick={() => {
                      void submitManualSync("full");
                    }}
                    disabled={syncTaskId !== null}
                    className="rounded-xl border border-emerald-200 bg-white px-4 py-3 text-sm font-black text-emerald-900 transition hover:border-emerald-400 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:border-stone-200 disabled:text-stone-400"
                  >
                    {syncTaskId && isFullHistorySync ? "Syncing all activities..." : "Sync all activities"}
                  </button>
                  <span
                    data-testid="sync-all-progress-count"
                    className="inline-flex items-center rounded-xl bg-stone-100 px-3 py-2 text-xs font-black uppercase tracking-[0.14em] text-stone-600"
                  >
                    {fullSyncProgressCountLabel}
                  </span>
                </div>
              ) : null}

              {!connection && !isStravaUnavailable ? (
                <div className="mt-4 flex flex-wrap gap-3">
                  <a
                    data-testid="connect-strava"
                    href={buildStravaLoginUrl()}
                    className="inline-flex rounded-xl bg-orange-500 px-4 py-3 text-sm font-black text-white transition hover:bg-orange-600"
                  >
                    Connect Strava
                  </a>
                  <button
                    type="button"
                    onClick={() => {
                      setShowStravaSetup((current) => !current);
                      setStravaSetupError(null);
                    }}
                    className="rounded-xl border border-stone-200 px-4 py-3 text-sm font-black text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                  >
                    {showStravaSetup ? "Hide app settings" : "Set up Strava app"}
                  </button>
                </div>
              ) : null}

              {!connection && (isStravaUnavailable || showStravaSetup) ? (
                <form className="mt-4 space-y-3" onSubmit={submitStravaSettings}>
                  <div>
                    <label
                      htmlFor="strava-client-id"
                      className="mb-1 block text-[11px] font-black uppercase tracking-[0.2em] text-stone-500"
                    >
                      Strava app ID
                    </label>
                    <input
                      id="strava-client-id"
                      data-testid="strava-client-id-input"
                      type="number"
                      min="1"
                      inputMode="numeric"
                      value={stravaClientId}
                      onChange={(event) => setStravaClientId(event.target.value)}
                      className="w-full rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-900 outline-none transition focus:border-emerald-300"
                      placeholder="Enter your Strava app ID"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="strava-client-secret"
                      className="mb-1 block text-[11px] font-black uppercase tracking-[0.2em] text-stone-500"
                    >
                      Strava client secret
                    </label>
                    <input
                      id="strava-client-secret"
                      data-testid="strava-client-secret-input"
                      type="password"
                      value={stravaClientSecret}
                      onChange={(event) => setStravaClientSecret(event.target.value)}
                      className="w-full rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-900 outline-none transition focus:border-emerald-300"
                      placeholder="Enter your Strava client secret"
                    />
                  </div>
                  {stravaSetupError ? (
                    <p
                      data-testid="strava-setup-error"
                      className="text-sm font-medium text-rose-700"
                    >
                      {stravaSetupError}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="submit"
                      data-testid="save-strava-settings"
                      disabled={savingStravaSetup}
                      className="rounded-xl bg-emerald-800 px-4 py-3 text-sm font-black text-white transition hover:bg-emerald-900 disabled:cursor-not-allowed disabled:bg-emerald-400"
                    >
                      {savingStravaSetup ? "Saving..." : "Save Strava app credentials"}
                    </button>
                    {!isStravaUnavailable ? (
                      <button
                        type="button"
                        onClick={() => {
                          setShowStravaSetup(false);
                          setStravaSetupError(null);
                        }}
                        className="rounded-xl border border-stone-200 px-4 py-3 text-sm font-black text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                      >
                        Cancel
                      </button>
                    ) : null}
                  </div>
                </form>
              ) : null}
            </div>
          </section>

          <section className="sticky top-4 z-[5] rounded-[28px] border border-stone-200/80 bg-[#fffdf8]/95 p-4 shadow-[0_20px_50px_-32px_rgba(15,23,42,0.55)] backdrop-blur">
            <div className="mb-3 flex items-end justify-between gap-4">
              <div>
                <p className="text-[11px] font-black uppercase tracking-[0.25em] text-stone-500">
                  Explore
                </p>
                <h2 className="mt-1 text-lg font-black text-stone-900">{listTitle}</h2>
              </div>
              <span className="rounded-full bg-stone-100 px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-stone-600">
                {listSummaryLabel}
              </span>
            </div>

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

            <div className="mt-4 grid grid-cols-3 gap-2">
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
                  {value === "remaining" ? "To bag" : value}
                </button>
              ))}
            </div>

            <div className="mt-4 flex items-center justify-between rounded-2xl bg-stone-100/70 px-3 py-2 text-[10px] font-black uppercase tracking-[0.25em] text-stone-500">
              <span>Sort</span>
              <div className="flex rounded-full bg-white p-1 shadow-sm">
                <button
                  type="button"
                  onClick={() => setSortBy("height")}
                  className={`rounded-full px-3 py-1 transition ${
                    sortBy === "height" ? "bg-emerald-900 text-white" : "text-stone-500 hover:text-stone-900"
                  }`}
                >
                  Height
                </button>
                <button
                  type="button"
                  onClick={() => setSortBy("alphabetical")}
                  className={`rounded-full px-3 py-1 transition ${
                    sortBy === "alphabetical"
                      ? "bg-emerald-900 text-white"
                      : "text-stone-500 hover:text-stone-900"
                  }`}
                >
                  A-Z
                </button>
              </div>
            </div>
          </section>

          <section className="space-y-2 pb-2">
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
                className="flex w-full items-center justify-between rounded-[24px] border border-transparent bg-white/92 p-3.5 text-left shadow-sm transition hover:border-stone-200 hover:bg-stone-50"
              >
                <div>
                  <h3 className="text-sm font-bold text-stone-900">{munro.name}</h3>
                  <p className="text-xs text-stone-500">{formatMetres(munro.height_metres)}</p>
                </div>
                <div className="flex items-center gap-2">
                  {(munro.bag_count ?? 0) > 1 ? (
                    <span className="rounded-full bg-emerald-100 px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] text-emerald-800">
                      {munro.bag_count}x
                    </span>
                  ) : null}
                  <div
                    className={`h-2.5 w-2.5 rounded-full ${
                      munro.is_bagged ? "bg-emerald-600" : "bg-rose-500"
                    }`}
                  />
                </div>
              </button>
            ))}
            {displayedMunros.length === 0 ? (
              <p className="rounded-[24px] border border-dashed border-stone-200 bg-white/75 px-4 py-8 text-center text-sm text-stone-500">
                No Munros match that search.
              </p>
            ) : null}
          </section>
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

        <div className="absolute right-4 top-4 z-[1000] space-y-2 rounded-[22px] bg-white/90 p-2 shadow-xl backdrop-blur">
          <div className="flex flex-wrap gap-2">
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
          <div className="rounded-2xl border border-stone-200/80 bg-white/80 p-2">
            <p className="px-2 pb-2 text-[10px] font-black uppercase tracking-[0.22em] text-stone-500">
              Map display
            </p>
            <div className="grid grid-cols-3 gap-2">
              {(
                [
                  { value: "all", label: "All" },
                  { value: "bagged", label: "Bagged" },
                  { value: "unbagged", label: "Unbagged" },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  data-testid={`map-filter-${option.value}`}
                  onClick={() => setMapMunroFilter(option.value)}
                  className={`rounded-2xl px-3 py-2 text-[11px] font-black uppercase tracking-[0.16em] transition ${
                    mapMunroFilter === option.value
                      ? "bg-stone-900 text-white"
                      : "text-stone-600 hover:bg-stone-100"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <MapContainer
          center={[56.817, -4.183]}
          zoom={7}
          style={{ height: "100%", width: "100%" }}
          zoomControl={false}
        >
          <TileLayer attribution={activeMap.attribution} url={activeMap.url} />
          <SetMapBounds munros={displayedMapMunros} />
          <MapController targetMunro={selectedMunro} />
          {displayedMapMunros.map((munro) => (
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
                  {munro.is_bagged && munro.bagged_at ? (
                    <div className="mt-3 border-t border-stone-200 pt-3 text-xs text-stone-600">
                      <p className="font-semibold text-stone-700">
                        Bagged on {formatLastBagDate(munro.bagged_at)}
                      </p>
                      {(munro.bag_count ?? 1) > 1 ? (
                        <p className="mt-1 font-medium text-stone-600">
                          Bagged {munro.bag_count} times
                        </p>
                      ) : null}
                      {(munro.bag_activities ?? []).length > 0 ? (
                        <div className="mt-2 flex flex-col gap-1.5">
                          {(munro.bag_activities ?? []).map((activity) => (
                            <a
                              key={`${munro.id}-${activity.source_activity_id}`}
                              href={`https://www.strava.com/activities/${activity.source_activity_id}`}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex font-black text-emerald-800 transition hover:text-emerald-950"
                            >
                              {`${(activity.name || "Strava activity").trim()} (${formatActivityLinkDate(activity.activity_date)})`}
                            </a>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
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
