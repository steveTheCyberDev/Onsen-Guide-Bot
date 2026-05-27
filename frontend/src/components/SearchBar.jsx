import { useState } from "react";

export default function SearchBar({ onSend, disabled }) {
  const [value, setValue] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    if (!value.trim()) return;
    onSend(value.trim());
    setValue("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2 p-4 border-t border-gray-200">
      <input
        className="flex-1 rounded-full border border-gray-300 px-4 py-2 text-sm outline-none focus:border-[#C45C3A]"
        placeholder="Ask about onsen in Japan..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={disabled}
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="rounded-full bg-[#C45C3A] px-5 py-2 text-sm text-white disabled:opacity-50"
      >
        Send
      </button>
    </form>
  );
}
