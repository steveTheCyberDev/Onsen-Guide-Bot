import { useEffect, useRef } from 'react';
import Message from './Message';

/**
 * MessageList — scrollable list of chat messages.
 * aria-live="polite" announces new messages to screen readers.
 */
export default function MessageList({ messages }) {
  const bottomRef = useRef(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div
      className="flex-1 overflow-y-auto px-3 py-4 space-y-3"
      aria-live="polite"
      aria-label="Chat messages"
      role="log"
    >
      {messages.map((msg, i) => (
        <Message
          key={i}
          role={msg.role}
          content={msg.content}
          onsens={msg.onsens}
        />
      ))}
      <div ref={bottomRef} aria-hidden="true" />
    </div>
  );
}
