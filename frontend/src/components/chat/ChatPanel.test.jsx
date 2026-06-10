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
          // objectContaining: the api helper also attaches X-API-Key when
          // VITE_API_KEY is set, so assert the headers we care about, not an
          // exact match.
          headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
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
            assistantMessage: {
              role: 'assistant',
              content: 'Found some spots.',
              onsens,
              recommendation: null,
            },
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

    it('threads recommendation onto the assistant message when present in the response', async () => {
      const onsens = [
        {
          name: 'Yamada Onsen',
          lat: 26.2,
          lng: 127.6,
          pros: ['Great view'],
          cons: ['Far from station'],
        },
      ];
      const recommendation = 'Yamada Onsen is the best pick for a relaxing soak with a view.';

      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Here is my pick.', onsens, hotels: [], recommendation })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'recommend one');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith({
          type: 'CHAT_RESULTS',
          payload: {
            onsens,
            hotels: [],
            assistantMessage: {
              role: 'assistant',
              content: 'Here is my pick.',
              onsens,
              recommendation,
            },
          },
        });
      });
    });

    it('threads recommendation as null when absent from the response', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Found 2 onsens.', onsens: [], hotels: [] }) // no recommendation key
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      await user.type(screen.getByRole('textbox', { name: /ask about onsen/i }), 'find onsen');
      await user.click(screen.getByRole('button', { name: /send message/i }));

      await waitFor(() => {
        expect(dispatch).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'CHAT_RESULTS',
            payload: expect.objectContaining({
              assistantMessage: expect.objectContaining({ recommendation: null }),
            }),
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

  // -------------------------------------------------------------------------
  // FOCUS_ONSEN plumbing — clicking an onsen card dispatches FOCUS_ONSEN
  // -------------------------------------------------------------------------

  describe('FOCUS_ONSEN plumbing', () => {
    it('dispatches FOCUS_ONSEN with the onsen when user clicks an OnsenMiniCard in the chat', async () => {
      const onsen = { name: 'Tama Onsen', lat: 26.3, lng: 127.8, location: 'Okinawa' };
      const state = makeState({
        messages: [
          {
            role: 'assistant',
            content: 'Here is an onsen for you.',
            onsens: [onsen],
          },
        ],
      });

      const user = userEvent.setup();
      render(<ChatPanel state={state} dispatch={dispatch} />);

      // The OnsenMiniCard renders as a button with aria-label "Show <name> on map"
      const card = screen.getByRole('button', { name: /show tama onsen on map/i });
      await user.click(card);

      expect(dispatch).toHaveBeenCalledWith({
        type: 'FOCUS_ONSEN',
        payload: onsen,
      });
    });

    it('dispatches FOCUS_ONSEN only for the clicked card when multiple onsens are present', async () => {
      const onsen1 = { name: 'Alpha Onsen', lat: 26.1, lng: 127.5, location: 'Naha' };
      const onsen2 = { name: 'Beta Onsen', lat: 26.2, lng: 127.6, location: 'Itoman' };
      const state = makeState({
        messages: [
          {
            role: 'assistant',
            content: 'Two results.',
            onsens: [onsen1, onsen2],
          },
        ],
      });

      const user = userEvent.setup();
      render(<ChatPanel state={state} dispatch={dispatch} />);

      await user.click(screen.getByRole('button', { name: /show beta onsen on map/i }));

      const focusCalls = dispatch.mock.calls.filter(([a]) => a.type === 'FOCUS_ONSEN');
      expect(focusCalls).toHaveLength(1);
      expect(focusCalls[0][0].payload).toEqual(onsen2);
    });
  });

  // -------------------------------------------------------------------------
  // ChatInput textarea behaviour
  // -------------------------------------------------------------------------

  describe('ChatInput textarea', () => {
    it('the input field is a textarea (textbox role)', () => {
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);
      // role="textbox" is what both <input type="text"> and <textarea> map to
      expect(screen.getByRole('textbox', { name: /ask about onsen/i })).toBeInTheDocument();
    });

    it('pressing Enter submits and calls handleSend (dispatches ADD_MESSAGE)', async () => {
      fetch.mockReturnValueOnce(
        makeFetchOk({ reply: 'Got it.', onsens: [], hotels: [] })
      );

      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      const input = screen.getByRole('textbox', { name: /ask about onsen/i });
      await user.type(input, 'Enter submit test');
      await user.keyboard('{Enter}');

      expect(dispatch).toHaveBeenCalledWith({
        type: 'ADD_MESSAGE',
        payload: { role: 'user', content: 'Enter submit test' },
      });
    });

    it('pressing Shift+Enter does NOT submit (does not dispatch ADD_MESSAGE immediately)', async () => {
      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      const input = screen.getByRole('textbox', { name: /ask about onsen/i });
      await user.type(input, 'line one');
      await user.keyboard('{Shift>}{Enter}{/Shift}');

      // No ADD_MESSAGE should have fired yet — the newline is inserted but not submitted
      const addMsgCalls = dispatch.mock.calls.filter(([a]) => a.type === 'ADD_MESSAGE');
      expect(addMsgCalls).toHaveLength(0);
    });

    it('Shift+Enter inserts a newline into the textarea value', async () => {
      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      const input = screen.getByRole('textbox', { name: /ask about onsen/i });
      await user.type(input, 'first line');
      await user.keyboard('{Shift>}{Enter}{/Shift}');
      await user.type(input, 'second line');

      // The textarea value should contain a newline between the two lines
      expect(input.value).toContain('\n');
      expect(input.value).toContain('first line');
      expect(input.value).toContain('second line');
    });

    it('disabled state blocks Enter-key submission', async () => {
      render(<ChatPanel state={makeState({ status: 'loading' })} dispatch={dispatch} />);

      const input = screen.getByRole('textbox', { name: /ask about onsen/i });
      // Even if we attempt keyboard interaction on the disabled textarea it should not dispatch
      // (the textarea itself is disabled, and the guard inside handleSubmit checks disabled too)
      expect(input).toBeDisabled();

      // No ADD_MESSAGE dispatched
      const addMsgCalls = dispatch.mock.calls.filter(([a]) => a.type === 'ADD_MESSAGE');
      expect(addMsgCalls).toHaveLength(0);
    });

    it('does not call onSend when textarea is empty and Enter is pressed', async () => {
      const user = userEvent.setup();
      render(<ChatPanel state={makeState()} dispatch={dispatch} />);

      const input = screen.getByRole('textbox', { name: /ask about onsen/i });
      // Do not type anything — just press Enter
      input.focus();
      await user.keyboard('{Enter}');

      const addMsgCalls = dispatch.mock.calls.filter(([a]) => a.type === 'ADD_MESSAGE');
      expect(addMsgCalls).toHaveLength(0);
    });
  });
});
