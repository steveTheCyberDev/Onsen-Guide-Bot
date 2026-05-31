import { useCallback, useEffect, useMemo, useRef } from 'react';
import { GoogleMap, useJsApiLoader } from '@react-google-maps/api';
import OnsenMarker from './OnsenMarker';
import HotelMarker from './HotelMarker';
import OnsenInfoStrip from './OnsenInfoStrip';
import ResultsSummaryBar from '../layout/ResultsSummaryBar';

const MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY ?? '';
const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

const DEFAULT_CENTER = { lat: 26.2124, lng: 127.6809 }; // Naha, Okinawa
const DEFAULT_ZOOM = 11;

const MAP_OPTIONS = {
  disableDefaultUI: false,
  zoomControl: true,
  mapTypeControl: false,
  streetViewControl: false,
  fullscreenControl: false,
  clickableIcons: false,
  styles: [
    {
      featureType: 'poi',
      elementType: 'labels',
      stylers: [{ visibility: 'off' }],
    },
    {
      featureType: 'transit',
      elementType: 'labels',
      stylers: [{ visibility: 'simplified' }],
    },
  ],
};

/**
 * MapPanel — centre panel wrapper.
 * Owns the GoogleMap instance and the /hotels API call.
 */
export default function MapPanel({ state, dispatch }) {
  const { onsens, hotels, selectedOnsen, selectedHotel, activeMarkers, selectedPrefecture, status } =
    state;

  const mapRef = useRef(null);

  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: MAPS_API_KEY,
    id: 'onsen-guide-map',
  });

  // Fit map bounds whenever onsens change
  useEffect(() => {
    if (!mapRef.current || !isLoaded || onsens.length === 0) return;

    const bounds = new window.google.maps.LatLngBounds();
    onsens.forEach((o) => {
      if (o.lat && o.lng) bounds.extend({ lat: o.lat, lng: o.lng });
    });
    if (!bounds.isEmpty()) {
      mapRef.current.fitBounds(bounds, 60); // 60px padding
    }
  }, [onsens, isLoaded]);

  // Re-centre map when prefecture changes
  useEffect(() => {
    if (!mapRef.current || !isLoaded || !selectedPrefecture) return;
    mapRef.current.panTo({ lat: selectedPrefecture.lat, lng: selectedPrefecture.lng });
    mapRef.current.setZoom(11);
  }, [selectedPrefecture, isLoaded]);

  // Pan to selected hotel marker
  useEffect(() => {
    if (!mapRef.current || !isLoaded || !selectedHotel) return;
    if (selectedHotel.lat && selectedHotel.lng) {
      mapRef.current.panTo({ lat: selectedHotel.lat, lng: selectedHotel.lng });
    }
  }, [selectedHotel, isLoaded]);

  const onMapLoad = useCallback((map) => {
    mapRef.current = map;
  }, []);

  const onMapUnmount = useCallback(() => {
    mapRef.current = null;
  }, []);

  // Memoize markers to avoid re-rendering all on unrelated state changes
  const onsenMarkers = useMemo(
    () =>
      onsens.map((onsen) => (
        <OnsenMarker
          key={onsen.name}
          onsen={onsen}
          isSelected={selectedOnsen?.name === onsen.name}
          onHover={(o) => dispatch({ type: 'HOVER_ONSEN', payload: o })}
          onHoverEnd={() => {}} // Keep strip visible until user closes or moves away
        />
      )),
    [onsens, selectedOnsen, dispatch]
  );

  const hotelMarkers = useMemo(
    () =>
      hotels.map((hotel) => (
        <HotelMarker
          key={hotel.name}
          hotel={hotel}
          isSelected={selectedHotel?.name === hotel.name}
          onSelect={(h) => dispatch({ type: 'SELECT_HOTEL', payload: h })}
        />
      )),
    [hotels, selectedHotel, dispatch]
  );

  async function handleSeeHotels(onsen) {
    if (!onsen?.lat || !onsen?.lng) return;

    dispatch({ type: 'SET_STATUS', payload: 'loading' });

    try {
      const res = await fetch(`${API_URL}/hotels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ latitude: onsen.lat, longitude: onsen.lng, radius: 3 }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      dispatch({ type: 'SHOW_HOTELS', payload: data.hotels ?? [] });
    } catch (err) {
      console.error('[MapPanel] /hotels error:', err);
      dispatch({ type: 'SHOW_HOTELS', payload: [] });
      dispatch({ type: 'SET_STATUS', payload: 'error' });
    }
  }

  function handleCloseStrip() {
    dispatch({ type: 'HOVER_ONSEN', payload: null });
  }

  function handleReset() {
    dispatch({ type: 'RESET' });
  }

  // Loading state before Maps JS API is ready
  if (loadError) {
    return (
      <div className="flex flex-col h-full items-center justify-center bg-[#FAF7F2] text-[#6B6B6B] text-sm px-6 text-center">
        <span className="text-3xl mb-3" aria-hidden="true">🗺️</span>
        <p>Map failed to load. Check your API key in .env.</p>
        <code className="mt-2 text-xs text-[#C9533A]">VITE_GOOGLE_MAPS_API_KEY</code>
      </div>
    );
  }

  if (!isLoaded) {
    return (
      <div className="flex flex-col h-full items-center justify-center bg-[#FAF7F2]">
        <div className="w-10 h-10 rounded-full border-4 border-[#C9533A] border-t-transparent animate-spin" role="status" aria-label="Loading map" />
        <p className="mt-3 text-sm text-[#6B6B6B]">Loading map…</p>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col h-full">
      {/* Results summary bar */}
      <ResultsSummaryBar
        onsens={onsens}
        selectedPrefecture={selectedPrefecture}
        onReset={handleReset}
      />

      {/* Map — fills remaining space */}
      <div className="flex-1 relative">
        <GoogleMap
          mapContainerStyle={{ width: '100%', height: '100%' }}
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          options={MAP_OPTIONS}
          onLoad={onMapLoad}
          onUnmount={onMapUnmount}
        >
          {onsenMarkers}
          {activeMarkers === 'both' && hotelMarkers}
        </GoogleMap>

        {/* Initial empty state prompt */}
        {onsens.length === 0 && status !== 'loading' && (
          <div className="absolute inset-0 flex items-end justify-center pointer-events-none pb-16">
            <div
              className="px-5 py-3 rounded-xl text-sm text-[#2C2C2C] text-center shadow-md pointer-events-auto"
              style={{ background: 'rgba(250,247,242,0.92)', backdropFilter: 'blur(4px)' }}
            >
              <span className="text-lg mr-2" aria-hidden="true">🔍</span>
              Search for onsen in the chat to see them on the map
            </div>
          </div>
        )}

        {/* Hotel loading overlay */}
        {status === 'loading' && hotels.length === 0 && onsens.length > 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-[rgba(250,247,242,0.6)] pointer-events-none">
            <div className="flex flex-col items-center gap-2">
              <div className="w-8 h-8 rounded-full border-4 border-[#C9533A] border-t-transparent animate-spin" aria-label="Loading hotels" />
              <span className="text-xs text-[#6B6B6B]">Finding nearby hotels…</span>
            </div>
          </div>
        )}
      </div>

      {/* OnsenInfoStrip — pinned to map bottom, not a popup */}
      <OnsenInfoStrip
        onsen={selectedOnsen}
        onSeeHotels={handleSeeHotels}
        onClose={handleCloseStrip}
      />
    </div>
  );
}
