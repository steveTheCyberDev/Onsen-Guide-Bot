export const initialState = {
  onsens: [],
  hotels: [],
  messages: [],
  selectedOnsen: null,
  selectedHotel: null,
  activeMarkers: 'onsens', // 'onsens' | 'both'
  selectedPrefecture: null,
  status: 'idle', // 'idle' | 'loading' | 'error'
  // focusCounter increments each time the user explicitly focuses an onsen from
  // the chat (via FOCUS_ONSEN). MapPanel watches this nonce — not selectedOnsen —
  // to trigger panTo+zoom, so hovering a marker never yanks the map.
  focusCounter: 0,
  // Per-conversation id sent to /chat as `session_id`. Must stay STABLE across
  // turns within one conversation (multi-turn history + the trip-planner's
  // per-thread checkpointer key off it) but a FRESH uuid whenever a new
  // conversation starts. RESET and SELECT_PREFECTURE both already clear
  // `messages` (i.e. they ARE "new conversation" actions), so both mint a new
  // sessionId below; every other action leaves it untouched.
  sessionId: createSessionId(),
};

/** Mint a per-conversation session id. Exported for reuse (e.g. App.jsx). */
export function createSessionId() {
  return crypto.randomUUID();
}

export function appReducer(state, action) {
  switch (action.type) {
    case 'CHAT_RESULTS': {
      // payload: { onsens, hotels, assistantMessage }
      // Appends the assistant reply to existing messages — no stale closure risk.
      // assistantMessage may carry a `recommendation` (string | null) — non-null
      // only in recommend mode with analyze enabled. It rides along on the
      // message object so MessageList/Message can render it inline; no
      // dedicated reducer handling needed.
      // If the chat response includes hotels (e.g. "show me X onsen and nearby
      // hotels"), show hotel markers too; otherwise just onsen markers.
      const hotels = action.payload.hotels ?? [];
      return {
        ...state,
        onsens: action.payload.onsens ?? [],
        hotels,
        messages: action.payload.assistantMessage
          ? [...state.messages, action.payload.assistantMessage]
          : state.messages,
        selectedOnsen: null,
        selectedHotel: null,
        activeMarkers: hotels.length > 0 ? 'both' : 'onsens',
        status: 'idle',
      };
    }

    case 'ADD_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.payload],
      };

    case 'HOVER_ONSEN':
      return {
        ...state,
        selectedOnsen: action.payload,
      };

    case 'SHOW_HOTELS':
      return {
        ...state,
        hotels: action.payload ?? [],
        activeMarkers: 'both',
        status: 'idle',
      };

    case 'SELECT_HOTEL':
      return {
        ...state,
        selectedHotel: action.payload,
      };

    case 'SELECT_PREFECTURE':
      // Clears messages → this IS a new conversation, so mint a fresh
      // sessionId (see comment on initialState.sessionId above).
      return {
        ...state,
        selectedPrefecture: action.payload,
        onsens: [],
        hotels: [],
        selectedOnsen: null,
        selectedHotel: null,
        activeMarkers: 'onsens',
        messages: [],
        status: 'idle',
        sessionId: createSessionId(),
      };

    case 'FOCUS_ONSEN':
      // Explicit user focus from the chat (e.g. clicking an OnsenMiniCard).
      // Sets selectedOnsen (highlights marker + shows OnsenInfoStrip) AND bumps
      // focusCounter so MapPanel can panTo+zoom without reacting to hover events.
      return {
        ...state,
        selectedOnsen: action.payload,
        focusCounter: state.focusCounter + 1,
      };

    case 'SET_STATUS':
      return {
        ...state,
        status: action.payload,
      };

    case 'RESET':
      // A brand-new conversation — mint a fresh sessionId rather than
      // reusing the module-level initialState one (which was already sent
      // to the backend for the conversation just reset).
      return {
        ...initialState,
        sessionId: createSessionId(),
      };

    default:
      return state;
  }
}
