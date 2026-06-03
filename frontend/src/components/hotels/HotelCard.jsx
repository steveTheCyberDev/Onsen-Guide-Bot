/**
 * HotelCard — individual hotel card.
 * hotel shape: { name, originalName, location, hotelSpecial, price, image, url, lat, lng, distance }
 */
export default function HotelCard({ hotel, isSelected, onSelect }) {
  if (!hotel) return null;

  function formatDistance(km) {
    if (km == null) return null;
    return km < 1 ? `${Math.round(km * 1000)}m away` : `${km.toFixed(1)}km away`;
  }

  function formatPrice(price) {
    if (!price) return null;
    return `¥${Number(price).toLocaleString()}/night`;
  }

  return (
    <article
      className={`rounded-xl border bg-white overflow-hidden cursor-pointer transition-all duration-200 hover:shadow-md focus-within:ring-2 focus-within:ring-[#C9533A] ${
        isSelected
          ? 'border-[#D9D0C5] shadow-md ring-2 ring-[#1F6F6B] ring-offset-1 ring-offset-white'
          : 'border-[#E8E0D5]'
      }`}
      onClick={() => onSelect(hotel)}
      aria-label={`Hotel: ${hotel.name}`}
    >
      {/* Image */}
      {hotel.image ? (
        <img
          src={hotel.image}
          alt={`Photo of ${hotel.name}`}
          className="w-full h-28 object-cover"
          loading="lazy"
        />
      ) : (
        <div className="w-full h-28 bg-[#F0EBE3] flex items-center justify-center">
          <span className="text-3xl" aria-hidden="true">🏨</span>
        </div>
      )}

      <div className="p-3">
        {/* Name */}
        <h3 className="font-semibold text-sm text-[#2C2C2C] truncate leading-tight">
          {hotel.name}
        </h3>
        {hotel.originalName && hotel.originalName !== hotel.name && (
          <p
            className="text-xs text-[#6B6B6B] truncate mt-0.5"
            style={{ fontFamily: "'Noto Sans JP', sans-serif" }}
          >
            {hotel.originalName}
          </p>
        )}

        {/* Location */}
        {hotel.location && (
          <p className="text-xs text-[#6B6B6B] mt-1 truncate flex items-center gap-1">
            <svg
              className="w-3 h-3 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
              />
            </svg>
            {hotel.location}
          </p>
        )}

        {/* Special feature */}
        {hotel.hotelSpecial && (
          <p className="text-xs text-[#2D6A4F] mt-1 line-clamp-2 leading-relaxed">
            {hotel.hotelSpecial}
          </p>
        )}

        {/* Distance + price row */}
        <div className="flex items-center justify-between mt-2">
          {hotel.distance != null && (
            <span className="text-xs text-[#6B6B6B]">{formatDistance(hotel.distance)}</span>
          )}
          {hotel.price && (
            <span className="text-xs font-semibold text-[#C9533A] ml-auto">
              {formatPrice(hotel.price)}
            </span>
          )}
        </div>

        {/* Book button */}
        {hotel.url ? (
          <a
            href={hotel.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="mt-2 flex items-center justify-center w-full px-3 py-1.5 rounded-lg bg-[#C9533A] text-white text-xs font-medium hover:bg-[#b04730] transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
            aria-label={`Book ${hotel.name} on Rakuten Travel`}
          >
            Book on Rakuten Travel
          </a>
        ) : (
          <button
            onClick={() => onSelect(hotel)}
            className="mt-2 flex items-center justify-center w-full px-3 py-1.5 rounded-lg border border-[#C9533A] text-[#C9533A] text-xs font-medium hover:bg-[#C9533A] hover:text-white transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-[#C9533A] focus:ring-offset-1"
            aria-label={`Select ${hotel.name} on map`}
          >
            Show on map
          </button>
        )}
      </div>
    </article>
  );
}
