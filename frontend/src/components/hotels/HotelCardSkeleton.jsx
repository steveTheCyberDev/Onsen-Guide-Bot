/**
 * HotelCardSkeleton — loading placeholder for hotel cards.
 * Uses pulse animation to indicate content is loading.
 */
export default function HotelCardSkeleton() {
  return (
    <div
      className="rounded-xl border border-[#E8E0D5] bg-white overflow-hidden animate-pulse"
      aria-hidden="true"
    >
      {/* Image placeholder */}
      <div className="w-full h-28 bg-[#E8E0D5]" />

      <div className="p-3 space-y-2">
        {/* Hotel name */}
        <div className="h-3 bg-[#E8E0D5] rounded-full w-3/4" />
        {/* Subtitle */}
        <div className="h-2.5 bg-[#E8E0D5] rounded-full w-1/2" />
        {/* Distance + price row */}
        <div className="flex justify-between mt-1">
          <div className="h-2.5 bg-[#E8E0D5] rounded-full w-1/4" />
          <div className="h-2.5 bg-[#E8E0D5] rounded-full w-1/4" />
        </div>
        {/* Button */}
        <div className="h-7 bg-[#E8E0D5] rounded-lg w-full mt-2" />
      </div>
    </div>
  );
}
