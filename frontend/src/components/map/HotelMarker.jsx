import { OverlayView } from '@react-google-maps/api';

/**
 * HotelMarker — smaller muted-blue marker with bed emoji.
 * Dispatches SELECT_HOTEL on click.
 */
export default function HotelMarker({ hotel, isSelected, onSelect }) {
  if (!hotel?.lat || !hotel?.lng) return null;

  const position = { lat: hotel.lat, lng: hotel.lng };

  return (
    <OverlayView
      position={position}
      mapPaneName={OverlayView.OVERLAY_MOUSE_TARGET}
      getPixelPositionOffset={(width, height) => ({
        x: -(width / 2),
        y: -(height / 2),
      })}
    >
      <button
        onClick={() => onSelect(hotel)}
        aria-label={`Hotel: ${hotel.name}`}
        title={hotel.name}
        className="flex items-center justify-center rounded-full border-2 cursor-pointer transition-transform duration-150 hover:scale-110 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-1"
        style={{
          width: '28px',
          height: '28px',
          backgroundColor: isSelected ? '#2d6ea8' : '#4A90D9',
          borderColor: isSelected ? '#FAF7F2' : 'rgba(255,255,255,0.8)',
          fontSize: '13px',
          boxShadow: isSelected
            ? '0 0 0 3px rgba(74,144,217,0.4), 0 2px 8px rgba(0,0,0,0.25)'
            : '0 2px 6px rgba(0,0,0,0.2)',
          transform: isSelected ? 'scale(1.15)' : undefined,
        }}
      >
        🛏
      </button>
    </OverlayView>
  );
}
