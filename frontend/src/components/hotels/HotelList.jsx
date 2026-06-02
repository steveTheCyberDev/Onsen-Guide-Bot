import { useEffect, useRef } from 'react';
import HotelCard from './HotelCard';

/**
 * HotelList — renders a sorted list of HotelCard components.
 * Also scrolls the selected hotel card into view when selectedHotel changes.
 */
export default function HotelList({ hotels, selectedHotel, onSelect }) {
  const selectedRef = useRef(null);

  // Scroll selected card into view when selectedHotel changes (from map click)
  useEffect(() => {
    if (selectedRef.current) {
      selectedRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [selectedHotel]);

  if (!hotels || hotels.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 px-4 text-center text-[#6B6B6B] text-sm">
        <span className="text-3xl mb-2" aria-hidden="true">🏨</span>
        <p>No hotels found nearby.</p>
        <p className="text-xs mt-1">Try a different onsen or expand the search radius.</p>
      </div>
    );
  }

  return (
    <ul className="space-y-3 px-3 py-3" aria-label="Nearby hotels list">
      {hotels.map((hotel) => {
        const isSelected = selectedHotel?.name === hotel.name;
        return (
          <li key={hotel.name} ref={isSelected ? selectedRef : null}>
            <HotelCard
              hotel={hotel}
              isSelected={isSelected}
              onSelect={onSelect}
            />
          </li>
        );
      })}
    </ul>
  );
}
