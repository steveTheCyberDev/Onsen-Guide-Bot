export default function Message({ role, content }) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2 text-sm ${
          isUser ? "bg-[#C45C3A] text-white" : "bg-[#F0EAE0] text-gray-800"
        }`}
      >
        {content}
      </div>
    </div>
  );
}
