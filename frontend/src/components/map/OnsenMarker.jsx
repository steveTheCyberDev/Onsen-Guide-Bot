import { OverlayView } from '@react-google-maps/api';

/**
 * OnsenMarker — circular warm-orange marker with 湯 character.
 * Dispatches HOVER_ONSEN on mouse enter/leave.
 */
export default function OnsenMarker({ onsen, isSelected, onHover, onHoverEnd }) {
  if (!onsen?.lat || !onsen?.lng) return null;

  const position = { lat: onsen.lat, lng: onsen.lng };

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
        onMouseEnter={() => onHover(onsen)}
        onMouseLeave={() => onHoverEnd()}
        aria-label={`Onsen: ${onsen.name}`}
        title={onsen.name}
        className="flex items-center justify-center rounded-full border-2 text-white font-bold cursor-pointer transition-transform duration-150 hover:scale-110 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-1"
        style={{
          width: '44px',
          height: '44px',
          backgroundColor: isSelected ? '#b04730' : '#C9533A',
          borderColor: isSelected ? '#FAF7F2' : 'rgba(255,255,255,0.8)',
          fontSize: '16px',
          fontFamily: "'Noto Sans JP', sans-serif",
          boxShadow: isSelected
            ? '0 0 0 3px rgba(201,83,58,0.4), 0 2px 8px rgba(0,0,0,0.25)'
            : '0 2px 6px rgba(0,0,0,0.2)',
          transform: isSelected ? 'scale(1.15)' : undefined,
        }}
      >
        湯
      </button>
    </OverlayView>
  );
}
