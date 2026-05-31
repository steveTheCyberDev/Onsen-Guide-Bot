import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatPanel from './ChatPanel';
import { initialState } from '../../reducer/appReducer';

// ---------------------------------------------------------------------------
// Stub import.meta.env so tests never need a real .env file.
// ChatPanel reads: import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
// Vitest exposes import.meta.env — we set it once at the module level and
// restore after each suite.
// ---------------------------------------------------------------------------
const FAKE_API_URL = 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal state object, overriding only what's needed for a test. */
function makeState(overrides = {}) {
  return { ...initialState, ...overrides };
}

/** Build a fetch response that resolves to a JSON body. */
function makeFetchOk(body) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  });
}

/** Build a fetch response that has a non-ok status. */
function makeFetchNotOk(status = 500) {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  });
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('ChatPanel', () => {
  let dispatch;

  beforeEach(() => {
    dispatch = vi.fn();
    // Stub global fetch before each test.
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // -------------------------------------------------------------------------
  // Empty state / initial render
  // -------------------------------------------------------------------------

  describe('initial render (no messages)', () => {
    it('shows the empty-state heading when there are no messages', () => {
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByText('Find your perfect onsen')).toBeInTheDocument();
    });

    it('shows the Chat panel header', () => {
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByText('Chat')).toBeInTheDocument();
    });

    it('renders the send input', () => {
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);
      expect(screen.getByRole('textbox', { name: /ask about onsen/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Messages rendered
  // -------------------------------------------------------------------------

  describe('message list', () => {
    it('renders the message log when messages are present', () => {
      const state = makeState({
        messages: [
          { role: 'user', content: 'Show me onsen in Okinawa' },
          { role: 'assistant', content: 'Here are some onsens!' },
        ],
      });
      render(<ChatPanel state={state} dispatch={dispatch} />);
      expect(screen.getByRole('log')).toBeInTheDocument();
      expect(screen.getByText('Show me onsen in Okinawa')).toBeInTheDocument();
      expect(screen.getByText('Here are some onsens!')).toBeInTheDocument();
    });

    it('does NOT render the empty state when messages are present', () => {
      const state = makeState({
        messages: [{ role: 'user', content: 'Hello' }],
      });
      render(<ChatPanel state={state} dispatch={dispatch} />);
      expect(screen.queryByText('Find your perfect onsen')).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // handleSend — happy path
  // -------------------------------------------------------------------------

  describe('handleSend — happy path', () => {
    it('dispatches ADD_MESSAGE then SET_STATUS loading when the user submits', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Found 2 onsens.', onsens: [], hotels: [] })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'Okinawa onsen');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      // First two dispatches must fire synchronously (before fetch settles).
      expect(dispatch).toHaveBeenNthCalledWith(1, {
        type: 'ADD_MESSAGE',
        payload: { role: 'user', content: 'Okinawa onsen' },
      });
      expect(dispatch).toHaveBeenNthCalledWith(2, {
        type: 'SET_STATUS',
        payload: 'loading',
      });
    });

    it('calls fetch with the correct URL, method, and body', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Done.', onsens: [], hotels: [] })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'test query');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      expect(fetch).toHaveBeenCalledWith(
        `${FAKE_API_URL}/chat`,
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: 'test query', session_id: 'default' }),
        })
      );
    });

    it('dispatches CHAT_RESULTS with onsens and hotels after fetch resolves', async () => {
      const onsens = [{ name: 'Yamada Onsen', lat: 26.2, lng: 127.6 }];
      const hotels = [{ name: 'Hotel A', lat: 26.21, lng: 127.68 }];

      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Found some spots.', onsens, hotels })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'find spots');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({
          type: 'CHAT_RESULTS',
          payload: {
            onsens,
            hotels,
            assistantMessage: { role: 'assistant', content: 'Found some spots.', onsens },
          },
        });
      });
    });

    it('defaults onsens and hotels to [] when they are absent from the response', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'No results.' }) // no onsens / hotels keys
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'any query');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'CHAT_RESULTS',
            payload: expect.objectContaining({ onsens: [], hotels: [] }),
          })
        );
      });
    });

    it('dispatches CHAT_RESULTS with hotels when response includes hotels (markers → both)', async () => {
      const hotels = [
        { name: 'Coral Hotel', lat: 26.22, lng: 127.7 },
        { name: 'Blue Wave Inn', lat: 26.23, lng: 127.71 },
      ];

      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Hotels nearby.', onsens: [], hotels })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'hotels');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        const chatResultsCall = dispatch.mock.calls.find(
          ([action]) => action.type === 'CHAT_RESULTS'
        );
        expect(chatResultsCall).toBeDefined();
        expect(chatResultsCall[0].payload.hotels).toEqual(hotels);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleSend — suggestion click shortcut
  // -------------------------------------------------------------------------

  describe('handleSend — suggestion click', () => {
    it('dispatches ADD_MESSAGE when user clicks a suggestion chip', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Here you go.', onsens: [], hotels: [] })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      // The empty state renders suggestion buttons.
      const suggestion = screen.getByRole('button', { name: 'Find onsen in Okinawa' });
      await user.click(suggestion);

      expect(dispatch).toHaveBeenCalledWith({
        type: 'ADD_MESSAGE',
        payload: { role: 'user', content: 'Find onsen in Okinawa' },
      });
    });
  });

  // -------------------------------------------------------------------------
  // Error path — fetch rejects
  // -------------------------------------------------------------------------

  describe('error path — fetch rejects', () => {
    beforeEach(() => {
      // Silence the console.error that ChatPanel logs in its catch block —
      // it's intentional production logging, not a test concern.
      vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    it('dispatches assistant error message and SET_STATUS error when fetch throws', async () => {
      // Use mockRejectedValue so the rejection is tied to the mock call and
      // does not become an unhandled rejection before the component awaits it.
      fetch.mockRejectedValueOnce(new Error('Network failure'));

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'crash test');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({
          type: 'ADD_MESSAGE',
          payload: { role: 'assistant', content: 'Something went wrong. Please try again.' },
        });
        expect(dispatch).toHaveBeenCalledWith({
          type: 'SET_STATUS',
          payload: 'error',
        });
      });
    });

    it('dispatches assistant error message and SET_STATUS error when fetch returns non-ok', async () => {
      fetch.mockReturnValueOnce(makeFetchNotOk(503));

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'server error');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({
          type: 'ADD_MESSAGE',
          payload: { role: 'assistant', content: 'Something went wrong. Please try again.' },
        });
        expect(dispatch).toHaveBeenCalledWith({
          type: 'SET_STATUS',
          payload: 'error',
        });
      });
    });

    it('does NOT dispatch CHAT_RESULTS on error', async () => {
      fetch.mockRejectedValueOnce(new Error('fail'));

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'boom');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ type: 'SET_STATUS' }));
      });

      const chatResultsCalls = dispatch.mock.calls.filter(
        ([action]) => action.type === 'CHAT_RESULTS'
      );
      expect(chatResultsCalls).toHaveLength(0);
    });
  });

  // -------------------------------------------------------------------------
  // Disabled input while loading
  // -------------------------------------------------------------------------

  describe('disabled state while loading', () => {
    it('disables the input and send button when status is loading', () => {
      render(<ChatPanel state={makeState({ status: 'loading' })} dispatch={dispatch} />);
      expect(screen.getByRole('textbox', { name: /ask about onsen/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled();
    });
  });
});
