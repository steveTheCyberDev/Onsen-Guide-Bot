import { useEffect, useRef } from 'react';
import HotelPanelEmpty from './HotelPanelEmpty';
import HotelList from './HotelList';
import HotelCardSkeleton from './HotelCardSkeleton';

/**
 * HotelPanel — right panel wrapper.
 * States: empty | loading | results | error
 */
export default function HotelPanel({ state, dispatch }) {
  const { hotels, selectedHotel, selectedOnsen, status, activeMarkers } = state;

  const panelRef = useRef(null);
  const isLoading = status === 'loading' && activeMarkers === 'onsens' && selectedOnsen != null;
  const showHotels = hotels.length > 0;
  const isError = status === 'error' && activeMarkers === 'both';

  // Move focus to panel when hotels slide in (a11y)
  useEffect(() => {
    if (showHotels && panelRef.current) {
      panelRef.current.focus();
    }
  }, [showHotels]);

  function handleSelect(hotel) {
    dispatch({ type: 'SELECT_HOTEL', payload: hotel });
  }

  return (
    <div
      ref={panelRef}
      className="flex flex-col h-full outline-none"
      tabIndex={-1}
      aria-label="Hotels panel"
    >
      {/* Panel header */}
      <div className="shrink-0 px-4 py-3 border-b border-[#D9D0C5] bg-[#FAF7F2]">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold text-[#6B6B6B] uppercase tracking-widest">
            Nearby Hotels
          </h2>
          {showHotels && (
            <span className="text-xs text-[#C9533A] font-medium">
              {hotels.length} found
            </span>
          )}
        </div>
        {selectedOnsen && (
          <p className="text-xs text-[#6B6B6B] mt-0.5 truncate">
            Near{' '}
            <span className="font-medium text-[#2C2C2C]">{selectedOnsen.name}</span>
          </p>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <ul className="space-y-3 px-3 py-3" aria-label="Loading hotels" aria-busy="true">
            {[1, 2, 3].map((n) => (
              <li key={n}>
                <HotelCardSkeleton />
              </li>
            ))}
          </ul>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center py-10 px-5 text-center">
            <span className="text-3xl mb-2" aria-hidden="true">⚠️</span>
            <p className="text-sm text-[#2C2C2C] font-medium">Could not load hotels</p>
            <p className="text-xs text-[#6B6B6B] mt-1">
              The hotel search failed. Please try again.
            </p>
          </div>
        ) : showHotels ? (
          <HotelList
            hotels={hotels}
            selectedHotel={selectedHotel}
            onSelect={handleSelect}
          />
        ) : (
          <HotelPanelEmpty />
        )}
      </div>
    </div>
  );
}
