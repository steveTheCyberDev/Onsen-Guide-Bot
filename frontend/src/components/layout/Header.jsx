import { useState } from 'react';

const PREFECTURES = [
  { label: 'Okinawa 沖縄',   value: 'Okinawa',   lat: 26.2124, lng: 127.6809 },
  { label: 'Aichi 愛知',     value: 'Aichi',     lat: 35.1815, lng: 136.9066 },
  { label: 'Gifu 岐阜',      value: 'Gifu',      lat: 35.3912, lng: 136.7223 },
  { label: 'Mie 三重',       value: 'Mie',       lat: 34.7303, lng: 136.5086 },
  { label: 'Shizuoka 静岡',  value: 'Shizuoka',  lat: 34.9756, lng: 138.3828 },
];

export default function Header({ selectedPrefecture, onSelectPrefecture }) {
  const [isOpen, setIsOpen] = useState(false);

  function handleSelect(prefecture) {
    onSelectPrefecture(prefecture);
    setIsOpen(false);
  }

  const current = PREFECTURES.find((p) => p.value === selectedPrefecture?.value);

  return (
    <header
      className="flex items-center justify-between px-6 bg-[#FAF7F2] border-b border-[#E8E0D5] z-20"
      style={{ height: '64px', minHeight: '64px', maxHeight: '64px' }}
    >
      {/* Title */}
      <div className="flex items-center gap-2">
        <span className="text-2xl" aria-hidden="true">🌸</span>
        <h1 className="font-serif text-xl font-semibold text-[#2C2C2C] tracking-tight">
          Onsen Guide
        </h1>
        <span
          className="text-sm text-[#C9533A] font-medium ml-1 opacity-70"
          style={{ fontFamily: "'Noto Sans JP', sans-serif" }}
          aria-label="onsen in Japanese"
        >
          温泉
        </span>
      </div>

      {/* Prefecture Dropdown */}
      <div className="relative">
        <button
          onClick={() => setIsOpen((v) => !v)}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-label="Select prefecture"
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-[#D9D0C5] bg-white text-sm text-[#2C2C2C] hover:border-[#C9533A] transition-colors duration-150 focus-ring"
        >
          <span>{current ? current.label : 'All Japan 日本'}</span>
          <svg
            className={`w-4 h-4 text-[#C9533A] transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {isOpen && (
          <ul
            role="listbox"
            aria-label="Prefecture options"
            className="absolute right-0 mt-1 w-52 bg-white border border-[#D9D0C5] rounded-lg shadow-lg z-50 py-1 overflow-hidden"
          >
            <li
              role="option"
              aria-selected={!selectedPrefecture}
              onClick={() => handleSelect(null)}
              className="px-4 py-2 text-sm cursor-pointer hover:bg-[#FAF7F2] text-[#2C2C2C]"
            >
              All Japan 日本
            </li>
            {PREFECTURES.map((p) => (
              <li
                key={p.value}
                role="option"
                aria-selected={selectedPrefecture?.value === p.value}
                onClick={() => handleSelect(p)}
                className={`px-4 py-2 text-sm cursor-pointer hover:bg-[#FAF7F2] text-[#2C2C2C] ${
                  selectedPrefecture?.value === p.value ? 'bg-[#F0EBE3] font-medium' : ''
                }`}
              >
                {p.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </header>
  );
}

export { PREFECTURES };
