import React, { useState, useEffect, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// --- Constants ---
const OSM_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const TOPO_URL = 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png'; // Backup for "OS Map" look
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
const USER_ID = import.meta.env.VITE_USER_ID || undefined;

// --- Icons (Memoized Singletons) ---
const baggedPeakIcon = L.divIcon({
  html: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 4L4 18H20L12 4Z" fill="#22c55e" stroke="white" stroke-width="2" stroke-linejoin="round"/>
    </svg>
  `,
  className: 'custom-peak-icon',
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -20],
});

const remainingPeakIcon = L.divIcon({
  html: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 4L4 18H20L12 4Z" fill="#ef4444" stroke="white" stroke-width="2" stroke-linejoin="round"/>
    </svg>
  `,
  className: 'custom-peak-icon',
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -20],
});

const getPeakIcon = (isBagged: boolean) => (isBagged ? baggedPeakIcon : remainingPeakIcon);

// --- Types ---
interface Munro {
  id: number;
  name: string;
  height_metres: number;
  latitude: number;
  longitude: number;
  is_bagged: boolean;
}

// --- Components ---

const SetMapBounds = ({ munros }: { munros: Munro[] }) => {
  const map = useMap();
  useEffect(() => {
    if (munros.length > 0) {
      const bounds = L.latLngBounds(munros.map(m => [m.latitude, m.longitude]));
      map.fitBounds(bounds, { padding: [50, 50] });
    }
  }, [munros, map]);
  return null;
};

const MapController = ({ targetMunro }: { targetMunro: Munro | null }) => {
  const map = useMap();
  useEffect(() => {
    if (targetMunro) {
      map.setView([targetMunro.latitude, targetMunro.longitude], 12, { animate: true });
    }
  }, [targetMunro, map]);
  return null;
};

const App: React.FC = () => {
  const [munros, setMunros] = useState<Munro[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [mapType, setMapType] = useState<'osm' | 'topo'>('osm');
  const [searchQuery, setSearchQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'bagged' | 'remaining'>('all');
  const [sortBy, setSortBy] = useState<'height' | 'alphabetical'>('height');
  const [selectedMunro, setSelectedMunro] = useState<Munro | null>(null);

  useEffect(() => {
    const fetchMunros = async () => {
      try {
        setLoading(true);
        const url = new URL(`${API_BASE_URL}/munros`, window.location.origin);
        if (USER_ID) {
          url.searchParams.append('user_id', USER_ID);
        }
        url.searchParams.append('limit', '300');
        const response = await fetch(url.toString());
        if (!response.ok) throw new Error('Failed to fetch Munros');
        const data = await response.json();
        setMunros(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };
    fetchMunros();
  }, []);

  const stats = useMemo(() => {
    const total = munros.length;
    const bagged = munros.filter(m => m.is_bagged).length;
    const percentage = total > 0 ? ((bagged / total) * 100).toFixed(1) : '0.0';
    const totalBaggedHeight = munros.filter(m => m.is_bagged).reduce((acc, m) => acc + m.height_metres, 0);
    return { total, bagged, percentage, totalBaggedHeight };
  }, [munros]);

  const filteredMunros = useMemo(() => {
    return munros
      .filter(m => {
        const matchesSearch = m.name.toLowerCase().includes(searchQuery.toLowerCase());
        const matchesFilter =
          filter === 'all' ||
          (filter === 'bagged' && m.is_bagged) ||
          (filter === 'remaining' && !m.is_bagged);
        return matchesSearch && matchesFilter;
      })
      .sort((a, b) => {
        if (sortBy === 'height') return b.height_metres - a.height_metres;
        return a.name.localeCompare(b.name);
      });
  }, [munros, searchQuery, filter, sortBy]);

  if (loading) return (
    <div className="flex h-screen items-center justify-center bg-slate-50">
      <div className="text-center">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-emerald-700 border-t-transparent mx-auto mb-4"></div>
        <p className="text-emerald-900 font-medium">Loading Highlands...</p>
      </div>
    </div>
  );

  if (error) return (
    <div className="flex h-screen items-center justify-center bg-slate-50 p-6">
      <div className="max-w-md rounded-2xl bg-white p-8 shadow-xl text-center">
        <h2 className="text-rose-600 text-2xl font-bold mb-2">Error</h2>
        <p className="text-slate-600 mb-6">{error}</p>
        <button onClick={() => window.location.reload()} className="rounded-xl bg-emerald-700 px-6 py-2 text-white font-medium hover:bg-emerald-800 transition-colors">Retry</button>
      </div>
    </div>
  );

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-900 font-sans">
      {/* Mobile Overlay */}
      {isSidebarOpen && (
        <div className="fixed inset-0 z-[1001] bg-slate-900/40 backdrop-blur-sm lg:hidden" onClick={() => setIsSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside id="sidebar" className={`fixed inset-y-0 left-0 z-[1002] w-80 transform bg-white p-6 shadow-2xl transition-transform duration-300 ease-in-out lg:static lg:translate-x-0 ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex flex-col h-full">
          <div className="mb-8">
            <div className="flex items-center justify-between mb-4">
              <h1 className="text-2xl font-black tracking-tight text-emerald-900">MunroStream</h1>
              <button onClick={() => setIsSidebarOpen(false)} className="lg:hidden text-slate-400 hover:text-slate-600" aria-label="Close sidebar">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>

            <div className="rounded-2xl bg-emerald-50 p-4 border border-emerald-100">
              <div className="flex justify-between items-end mb-2">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-emerald-700 opacity-70">Progress</p>
                  <p className="text-2xl font-black text-emerald-900">{stats.bagged} / {stats.total}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs font-bold uppercase tracking-wider text-emerald-700 opacity-70">Total Ascent</p>
                  <p className="text-sm font-bold text-emerald-900">{stats.totalBaggedHeight.toLocaleString()}m</p>
                </div>
              </div>
              <div className="h-2 w-full rounded-full bg-emerald-200 overflow-hidden">
                <div className="h-full bg-emerald-600 transition-all duration-1000" style={{ width: `${stats.percentage}%` }} />
              </div>
              <p className="mt-2 text-right text-xs font-bold text-emerald-700">{stats.percentage}% Complete</p>
            </div>
          </div>

          <div className="space-y-4 mb-6">
            <div className="relative">
              <input
                type="text" placeholder="Search Munros..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl border-none bg-slate-100 px-4 py-2.5 pl-10 text-sm focus:ring-2 focus:ring-emerald-500/20"
              />
              <svg className="absolute left-3 top-3 text-slate-400" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            </div>

            <div className="grid grid-cols-3 gap-2">
              {(['all', 'bagged', 'remaining'] as const).map(f => (
                <button key={f} onClick={() => setFilter(f)} className={`rounded-lg py-1.5 text-xs font-bold capitalize transition-all ${filter === f ? 'bg-emerald-800 text-white shadow-md' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                  {f}
                </button>
              ))}
            </div>

            <div className="flex items-center justify-between text-[10px] font-black uppercase tracking-widest text-slate-400 px-1">
              <span>Sort by</span>
              <div className="flex gap-3">
                <button onClick={() => setSortBy('height')} className={`hover:text-emerald-700 ${sortBy === 'height' ? 'text-emerald-700' : ''}`}>Height</button>
                <button onClick={() => setSortBy('alphabetical')} className={`hover:text-emerald-700 ${sortBy === 'alphabetical' ? 'text-emerald-700' : ''}`}>A-Z</button>
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto -mx-2 px-2 space-y-2 custom-scrollbar">
            {filteredMunros.map(m => (
              <button
                key={m.id} onClick={() => { setSelectedMunro(m); if(window.innerWidth < 1024) setIsSidebarOpen(false); }}
                className="w-full text-left group flex items-center justify-between rounded-xl border border-transparent bg-white p-3 hover:bg-slate-50 hover:border-slate-200 transition-all shadow-sm"
              >
                <div>
                  <h3 className="text-sm font-bold text-slate-800 group-hover:text-emerald-900 transition-colors">{m.name}</h3>
                  <p className="text-xs font-medium text-slate-400">{m.height_metres}m</p>
                </div>
                <div className={`h-2 w-2 rounded-full ${m.is_bagged ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              </button>
            ))}
            {filteredMunros.length === 0 && <p className="text-center text-sm text-slate-400 py-8">No matching peaks.</p>}
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="relative flex-1">
        <button onClick={() => setIsSidebarOpen(true)} className="absolute left-4 top-4 z-[1000] rounded-2xl bg-white p-3 shadow-xl lg:hidden hover:bg-slate-50" aria-label="Open menu" aria-expanded={isSidebarOpen} aria-controls="sidebar">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
        </button>

        <div className="absolute right-4 top-4 z-[1000] flex rounded-2xl bg-white/90 backdrop-blur p-1 shadow-xl border border-white">
          <button onClick={() => setMapType('osm')} className={`rounded-xl px-4 py-2 text-xs font-black transition-all ${mapType === 'osm' ? 'bg-emerald-800 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>OSM</button>
          <button onClick={() => setMapType('topo')} className={`rounded-xl px-4 py-2 text-xs font-black transition-all ${mapType === 'topo' ? 'bg-emerald-800 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>OS Map</button>
        </div>

        <MapContainer center={[56.817, -4.183]} zoom={7} style={{ height: '100%', width: '100%' }} zoomControl={false}>
          <TileLayer attribution="&copy; OpenStreetMap &copy; OpenTopoMap" url={mapType === 'osm' ? OSM_URL : TOPO_URL} />
          <SetMapBounds munros={munros} />
          <MapController targetMunro={selectedMunro} />
          {munros.map(m => (
            <Marker key={m.id} position={[m.latitude, m.longitude]} icon={getPeakIcon(m.is_bagged)}>
              <Popup className="custom-popup">
                <div className="p-1">
                  <h3 className="text-base font-black text-emerald-900 mb-1">{m.name}</h3>
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-sm font-bold text-slate-500">{m.height_metres}m</span>
                    <span className={`text-[10px] font-black uppercase tracking-widest ${m.is_bagged ? 'text-emerald-600' : 'text-rose-600'}`}>
                      {m.is_bagged ? 'Bagged ✓' : 'Remaining'}
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
