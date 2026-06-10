import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import Message from './Message';

describe('Message', () => {
  // -------------------------------------------------------------------------
  // Basic rendering
  // -------------------------------------------------------------------------

  it('renders the message content', () => {
    render(<Message role="user" content="Hello there" />);
    expect(screen.getByText('Hello there')).toBeInTheDocument();
  });

  it('renders onsen mini cards when assistant message has onsens', () => {
    const onsens = [{ name: 'Yamada Onsen', lat: 26.2, lng: 127.6 }];
    render(<Message role="assistant" content="Here you go" onsens={onsens} />);
    expect(screen.getByText('Yamada Onsen')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Guide recommendation callout
  // -------------------------------------------------------------------------

  describe('guide recommendation callout', () => {
    it('renders the recommendation callout with the recommendation text for an assistant message', () => {
      render(
        <Message
          role="assistant"
          content="Here are some great picks."
          recommendation="Yamada Onsen has the best view and is closest to the station."
        />
      );

      const callout = screen.getByTestId('guide-recommendation');
      expect(callout).toBeInTheDocument();
      expect(callout).toHaveTextContent('✨');
      expect(callout).toHaveTextContent(
        'Yamada Onsen has the best view and is closest to the station.'
      );
    });

    it('does NOT render the callout when recommendation is null', () => {
      render(<Message role="assistant" content="Here are some great picks." recommendation={null} />);
      expect(screen.queryByTestId('guide-recommendation')).not.toBeInTheDocument();
    });

    it('does NOT render the callout when recommendation is an empty string', () => {
      render(<Message role="assistant" content="Here are some great picks." recommendation="" />);
      expect(screen.queryByTestId('guide-recommendation')).not.toBeInTheDocument();
    });

    it('does NOT render the callout when recommendation is absent', () => {
      render(<Message role="assistant" content="Here are some great picks." />);
      expect(screen.queryByTestId('guide-recommendation')).not.toBeInTheDocument();
    });

    it('does NOT render the callout for a user message even if a recommendation value is present', () => {
      render(
        <Message
          role="user"
          content="Find me an onsen"
          recommendation="This should never show for a user message."
        />
      );
      expect(screen.queryByTestId('guide-recommendation')).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // onSelectOnsen plumbing (sanity check, unaffected by recommendation)
  // -------------------------------------------------------------------------

  it('forwards onSelectOnsen to OnsenMiniCard so clicking a card fires the callback', async () => {
    const onsen = { name: 'Tama Onsen', lat: 26.3, lng: 127.8 };
    const onSelectOnsen = vi.fn();
    const { default: userEvent } = await import('@testing-library/user-event');

    render(
      <Message
        role="assistant"
        content="Result"
        onsens={[onsen]}
        onSelectOnsen={onSelectOnsen}
      />
    );

    await userEvent.click(screen.getByRole('button', { name: /show tama onsen on map/i }));
    expect(onSelectOnsen).toHaveBeenCalledWith(onsen);
  });
});
