import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix for default markers in react-leaflet
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

type Munro = {
  id: number;
  name: string;
  height_metres: number;
  latitude: number;
  longitude: number;
  bagged?: boolean; // For now, we'll simulate this
};

type MapTileType = 'osm' | 'os';

const OS_MAP_URL = 'https://api.os.uk/maps/raster/v1/zxy/Light_3857/{z}/{x}/{y}.png?key=YOUR_OS_API_KEY';
const OSM_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

export default function App() {
  const [munros, setMunros] = useState<Munro[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mapTileType, setMapTileType] = useState<MapTileType>('osm');
  const [selectedMunro, setSelectedMunro] = useState<Munro | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${import.meta.env.VITE_API_BASE_URL || "/api/v1"}/munros?limit=300`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`API returned ${response.status}`);
        }
        return response.json() as Promise<Munro[]>;
      })
      .then((data) => {
        // Simulate bagging status for demo - in real app this would come from user data
        const munrosWithBagging = data.map((munro, index) => ({
          ...munro,
          bagged: index % 3 === 0 // Every 3rd Munro is "bagged" for demo
        }));
        setMunros(munrosWithBagging);
        setLoading(false);
        setError(null);
      })
      .catch((fetchError: unknown) => {
        if (fetchError instanceof Error && fetchError.name !== "AbortError") {
          setError(fetchError.message);
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, []);

  const createMarkerIcon = (bagged: boolean) => {
    return L.divIcon({
      className: 'custom-munro-marker',
      html: `<div style="
        width: 20px;
        height: 20px;
        background-color: ${bagged ? '#22c55e' : '#ef4444'};
        border: 2px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        color: white;
        font-weight: bold;
      ">▲</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-mist via-white to-heather flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-pine mx-auto mb-4"></div>
          <p className="text-peat">Loading Munros...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-mist via-white to-heather flex items-center justify-center">
        <div className="text-center max-w-md mx-auto p-6">
          <p className="text-ember mb-4">Failed to load Munros</p>
          <p className="text-heather">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-mist via-white to-heather">
      <div className="flex h-screen">
        {/* Sidebar */}
        <div className="w-full md:w-80 bg-white/90 backdrop-blur shadow-ridge overflow-y-auto">
          <div className="p-6 border-b border-pine/10">
            <h1 className="text-2xl font-display font-bold text-peat mb-2">Munro Tracker</h1>
            <p className="text-heather text-sm">Track your Scottish Munro bagging progress</p>
          </div>

          {/* Map Type Toggle */}
          <div className="p-4 border-b border-pine/10">
            <div className="flex gap-2">
              <button
                onClick={() => setMapTileType('osm')}
                className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-colors ${
                  mapTileType === 'osm'
                    ? 'bg-pine text-white'
                    : 'bg-mist text-peat hover:bg-heather/20'
                }`}
              >
                OpenStreetMap
              </button>
              <button
                onClick={() => setMapTileType('os')}
                className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-colors ${
                  mapTileType === 'os'
                    ? 'bg-pine text-white'
                    : 'bg-mist text-peat hover:bg-heather/20'
                }`}
              >
                OS Map
              </button>
            </div>
          </div>

          {/* Munro List */}
          <div className="p-4">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-peat">Munros</h2>
              <div className="flex gap-4 text-sm">
                <div className="flex items-center gap-1">
                  <div className="w-3 h-3 bg-green-500 rounded-full"></div>
                  <span className="text-heather">Bagged</span>
                </div>
                <div className="flex items-center gap-1">
                  <div className="w-3 h-3 bg-red-500 rounded-full"></div>
                  <span className="text-heather">Remaining</span>
                </div>
              </div>
            </div>

            <div className="space-y-2 max-h-96 overflow-y-auto">
              {munros.map((munro) => (
                <div
                  key={munro.id}
                  onClick={() => setSelectedMunro(munro)}
                  className={`p-3 rounded-lg cursor-pointer transition-colors ${
                    selectedMunro?.id === munro.id
                      ? 'bg-pine/10 border border-pine/20'
                      : 'hover:bg-mist/50'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <h3 className="font-medium text-peat text-sm">{munro.name}</h3>
                      <p className="text-heather text-xs">{munro.height_metres}m</p>
                    </div>
                    <div className={`w-4 h-4 rounded-full ${munro.bagged ? 'bg-green-500' : 'bg-red-500'}`}></div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Map */}
        <div className="flex-1 relative">
          <MapContainer
            center={[56.8169, -4.1826]} // Center of Scotland
            zoom={7}
            style={{ height: '100%', width: '100%' }}
            className="z-0"
          >
            <TileLayer
              url={mapTileType === 'os' ? OS_MAP_URL : OSM_URL}
              attribution={mapTileType === 'os'
                ? '&copy; <a href="https://www.ordnancesurvey.co.uk/">Ordnance Survey</a>'
                : '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              }
            />
            {munros.map((munro) => (
              <Marker
                key={munro.id}
                position={[munro.latitude, munro.longitude]}
                icon={createMarkerIcon(munro.bagged || false)}
                eventHandlers={{
                  click: () => setSelectedMunro(munro),
                }}
              >
                <Popup>
                  <div className="text-center">
                    <h3 className="font-semibold text-peat">{munro.name}</h3>
                    <p className="text-heather text-sm">{munro.height_metres}m</p>
                    <p className={`text-xs font-medium ${munro.bagged ? 'text-green-600' : 'text-red-600'}`}>
                      {munro.bagged ? 'Bagged ✓' : 'Remaining'}
                    </p>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MapContainer>
        </div>
      </div>
    </div>
  );
}

    return () => controller.abort();
  }, []);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(178,197,114,0.28),_transparent_35%),linear-gradient(160deg,_#f7f4ee_0%,_#dce6e4_48%,_#c5d3cf_100%)] text-peat">
      <section className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10 lg:px-10">
        <div className="grid gap-8 lg:grid-cols-[1.35fr_0.95fr]">
          <div className="space-y-6 rounded-[2rem] border border-white/60 bg-white/70 p-8 shadow-ridge backdrop-blur">
            <p className="text-sm font-semibold uppercase tracking-[0.32em] text-pine">
              MunroStream local environment
            </p>
            <div className="space-y-4">
              <h1 className="font-display text-5xl leading-tight text-peat sm:text-6xl">
                Build a reliable Munro bagging platform before adding product polish.
              </h1>
              <p className="max-w-2xl text-lg leading-8 text-heather">
                This starter stack pairs a stateless FastAPI service with PostGIS, Redis, Celery,
                and a Vite client. Every summit decision is designed around metric distances,
                refresh-token-safe OAuth, and DoBIH-backed Munro data integrity.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard label="Summit threshold" value="100 m" />
              <StatCard label="Coordinate SRID" value="4326" />
              <StatCard label="Distance units" value="Kilometres" />
            </div>
          </div>

          <aside className="rounded-[2rem] border border-pine/15 bg-peat p-8 text-mist shadow-ridge">
            <p className="text-sm font-semibold uppercase tracking-[0.32em] text-lichen">
              Service health
            </p>
            <div className="mt-6 space-y-4">
              <HealthRow label="API" value={health?.status ?? "Waiting"} tone={health ? "ok" : "idle"} />
              <HealthRow label="Database" value={health?.database ?? "Unknown"} tone={health?.database === "ok" ? "ok" : "idle"} />
              <HealthRow label="Redis" value={health?.redis ?? "Unknown"} tone={health?.redis === "ok" ? "ok" : "idle"} />
              <p className="rounded-2xl border border-white/10 bg-white/5 p-4 text-sm leading-6 text-granite">
                {error
                  ? `The API health endpoint is not available yet: ${error}.`
                  : health?.postgis
                    ? `PostGIS reports: ${health.postgis}`
                    : "Start the compose stack to verify FastAPI, PostgreSQL/PostGIS, and Redis together."}
              </p>
            </div>
          </aside>
        </div>

        <section className="mt-8 grid gap-5 lg:grid-cols-3">
          {milestones.map((milestone) => (
            <article
              key={milestone.title}
              className="rounded-[1.75rem] border border-pine/10 bg-white/65 p-6 shadow-ridge backdrop-blur"
            >
              <p className="text-sm font-semibold uppercase tracking-[0.24em] text-ember">
                {milestone.title}
              </p>
              <p className="mt-4 text-base leading-7 text-heather">{milestone.detail}</p>
            </article>
          ))}
        </section>
      </section>
    </main>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[1.5rem] border border-pine/10 bg-pine/5 p-4">
      <p className="text-sm uppercase tracking-[0.18em] text-heather">{label}</p>
      <p className="mt-3 text-2xl font-semibold text-pine">{value}</p>
    </div>
  );
}

function HealthRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "idle";
}) {
  return (
    <div className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
      <span className="text-sm uppercase tracking-[0.24em] text-granite">{label}</span>
      <span
        className={
          tone === "ok"
            ? "rounded-full bg-lichen/20 px-3 py-1 text-sm font-semibold text-lichen"
            : "rounded-full bg-white/10 px-3 py-1 text-sm font-semibold text-mist"
        }
      >
        {value}
      </span>
    </div>
  );
}
