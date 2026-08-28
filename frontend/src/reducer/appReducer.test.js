import { describe, it, expect } from 'vitest';
import { appReducer, initialState } from './appReducer';

const onsen = { name: 'Yamada Onsen', lat: 26.2, lng: 127.6 };
const hotel = { name: 'グレイス那覇', lat: 26.21, lng: 127.68 };

describe('appReducer', () => {
  it('returns the current state for an unknown action', () => {
    const state = { ...initialState, status: 'loading' };
    expect(appReducer(state, { type: 'NOPE' })).toBe(state);
  });

  describe('CHAT_RESULTS', () => {
    it('sets onsens and appends the assistant message', () => {
      const start = { ...initialState, messages: [{ role: 'user', content: 'hi' }] };
      const next = appReducer(start, {
        type: 'CHAT_RESULTS',
        payload: {
          onsens: [onsen],
          assistantMessage: { role: 'assistant', content: 'Found 1 onsen.' },
        },
      });
      expect(next.onsens).toEqual([onsen]);
      expect(next.messages).toHaveLength(2);
      expect(next.messages[1]).toEqual({ role: 'assistant', content: 'Found 1 onsen.' });
    });

    it("keeps activeMarkers 'onsens' when no hotels are returned", () => {
      const next = appReducer(initialState, {
        type: 'CHAT_RESULTS',
        payload: { onsens: [onsen] },
      });
      expect(next.hotels).toEqual([]);
      expect(next.activeMarkers).toBe('onsens');
    });

    it("flips activeMarkers to 'both' and sets hotels when hotels are returned", () => {
      const next = appReducer(initialState, {
        type: 'CHAT_RESULTS',
        payload: { onsens: [onsen], hotels: [hotel] },
      });
      expect(next.hotels).toEqual([hotel]);
      expect(next.activeMarkers).toBe('both');
    });

    it('resets prior selections and status', () => {
      const start = {
        ...initialState,
        selectedOnsen: onsen,
        selectedHotel: hotel,
        status: 'loading',
      };
      const next = appReducer(start, { type: 'CHAT_RESULTS', payload: { onsens: [] } });
      expect(next.selectedOnsen).toBeNull();
      expect(next.selectedHotel).toBeNull();
      expect(next.status).toBe('idle');
    });

    it('defaults onsens to an empty array when omitted', () => {
      const next = appReducer(initialState, { type: 'CHAT_RESULTS', payload: {} });
      expect(next.onsens).toEqual([]);
    });

    it('does not mutate the input state', () => {
      const start = { ...initialState, messages: [] };
      const frozen = Object.freeze({ ...start, messages: Object.freeze([]) });
      expect(() =>
        appReducer(frozen, {
          type: 'CHAT_RESULTS',
          payload: { onsens: [onsen], assistantMessage: { role: 'assistant', content: 'x' } },
        })
      ).not.toThrow();
    });
  });

  describe('ADD_MESSAGE', () => {
    it('appends a message without touching other fields', () => {
      const next = appReducer(initialState, {
        type: 'ADD_MESSAGE',
        payload: { role: 'user', content: 'hello' },
      });
      expect(next.messages).toEqual([{ role: 'user', content: 'hello' }]);
    });
  });

  describe('HOVER_ONSEN', () => {
    it('sets selectedOnsen', () => {
      const next = appReducer(initialState, { type: 'HOVER_ONSEN', payload: onsen });
      expect(next.selectedOnsen).toEqual(onsen);
    });

    it('clears selectedOnsen when payload is null', () => {
      const start = { ...initialState, selectedOnsen: onsen };
      const next = appReducer(start, { type: 'HOVER_ONSEN', payload: null });
      expect(next.selectedOnsen).toBeNull();
    });
  });

  describe('SHOW_HOTELS', () => {
    it("sets hotels, flips activeMarkers to 'both', and resets status", () => {
      const start = { ...initialState, status: 'loading' };
      const next = appReducer(start, { type: 'SHOW_HOTELS', payload: [hotel] });
      expect(next.hotels).toEqual([hotel]);
      expect(next.activeMarkers).toBe('both');
      expect(next.status).toBe('idle');
    });

    it('defaults to an empty hotels array when payload is missing', () => {
      const next = appReducer(initialState, { type: 'SHOW_HOTELS', payload: undefined });
      expect(next.hotels).toEqual([]);
    });
  });

  describe('SELECT_HOTEL', () => {
    it('sets selectedHotel', () => {
      const next = appReducer(initialState, { type: 'SELECT_HOTEL', payload: hotel });
      expect(next.selectedHotel).toEqual(hotel);
    });
  });

  describe('SELECT_PREFECTURE', () => {
    it('sets the prefecture and clears results, selections, and messages', () => {
      const start = {
        ...initialState,
        onsens: [onsen],
        hotels: [hotel],
        selectedOnsen: onsen,
        selectedHotel: hotel,
        messages: [{ role: 'user', content: 'hi' }],
        activeMarkers: 'both',
      };
      const pref = { name: 'Okinawa', lat: 26.2, lng: 127.6 };
      const next = appReducer(start, { type: 'SELECT_PREFECTURE', payload: pref });
      expect(next.selectedPrefecture).toEqual(pref);
      expect(next.onsens).toEqual([]);
      expect(next.hotels).toEqual([]);
      expect(next.selectedOnsen).toBeNull();
      expect(next.selectedHotel).toBeNull();
      expect(next.messages).toEqual([]);
      expect(next.activeMarkers).toBe('onsens');
    });
  });

  describe('FOCUS_ONSEN', () => {
    it('sets selectedOnsen', () => {
      const next = appReducer(initialState, { type: 'FOCUS_ONSEN', payload: onsen });
      expect(next.selectedOnsen).toEqual(onsen);
    });

    it('increments focusCounter by 1 each dispatch', () => {
      const first = appReducer(initialState, { type: 'FOCUS_ONSEN', payload: onsen });
      expect(first.focusCounter).toBe(1);
      const second = appReducer(first, { type: 'FOCUS_ONSEN', payload: onsen });
      expect(second.focusCounter).toBe(2);
    });

    it('does not touch any other state fields', () => {
      const start = { ...initialState, hotels: [hotel], status: 'idle' };
      const next = appReducer(start, { type: 'FOCUS_ONSEN', payload: onsen });
      expect(next.hotels).toEqual([hotel]);
      expect(next.status).toBe('idle');
      expect(next.selectedHotel).toBeNull();
    });

    it('clears selectedOnsen when payload is null and still increments focusCounter', () => {
      const start = { ...initialState, selectedOnsen: onsen, focusCounter: 3 };
      const next = appReducer(start, { type: 'FOCUS_ONSEN', payload: null });
      expect(next.selectedOnsen).toBeNull();
      expect(next.focusCounter).toBe(4);
    });
  });

  describe('SET_STATUS', () => {
    it('updates only status', () => {
      const next = appReducer(initialState, { type: 'SET_STATUS', payload: 'error' });
      expect(next.status).toBe('error');
    });
  });

  describe('RESET', () => {
    it('returns the initial state (except sessionId, which is freshly minted)', () => {
      const start = { ...initialState, onsens: [onsen], hotels: [hotel], status: 'error' };
      const next = appReducer(start, { type: 'RESET' });
      expect(next).toEqual({ ...initialState, sessionId: next.sessionId });
    });

    it('mints a NEW sessionId distinct from the one it reset from', () => {
      const start = { ...initialState, onsens: [onsen], sessionId: 'prior-session-id' };
      const next = appReducer(start, { type: 'RESET' });
      expect(next.sessionId).toBeTruthy();
      expect(next.sessionId).not.toBe('prior-session-id');
    });
  });

  describe('sessionId', () => {
    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

    it('initialState carries a v4 UUID sessionId', () => {
      expect(initialState.sessionId).toMatch(UUID_RE);
    });

    it('is left untouched by actions that do not start a new conversation', () => {
      const start = { ...initialState };
      const next = appReducer(start, {
        type: 'CHAT_RESULTS',
        payload: { onsens: [onsen], assistantMessage: { role: 'assistant', content: 'hi' } },
      });
      expect(next.sessionId).toBe(start.sessionId);
    });

    it('SELECT_PREFECTURE mints a new sessionId (it clears messages → new conversation)', () => {
      const start = { ...initialState, sessionId: 'prior-session-id' };
      const pref = { name: 'Okinawa', lat: 26.2, lng: 127.6 };
      const next = appReducer(start, { type: 'SELECT_PREFECTURE', payload: pref });
      expect(next.sessionId).toBeTruthy();
      expect(next.sessionId).not.toBe('prior-session-id');
    });
  });
});
