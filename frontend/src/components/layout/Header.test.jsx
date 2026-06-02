import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Header, { PREFECTURES } from './Header';

describe('Header', () => {
  let onSelectPrefecture;

  beforeEach(() => {
    onSelectPrefecture = vi.fn();
  });

  // -------------------------------------------------------------------------
  // Static chrome
  // -------------------------------------------------------------------------

  it('renders the app title', () => {
    render(<Header onSelectPrefecture={onSelectPrefecture} />);
    expect(screen.getByText('Onsen Guide')).toBeInTheDocument();
  });

  it('shows "All Japan" on the dropdown trigger when no prefecture is selected', () => {
    render(<Header onSelectPrefecture={onSelectPrefecture} />);
    expect(
      screen.getByRole('button', { name: 'Select prefecture' })
    ).toHaveTextContent('All Japan 日本');
  });

  it('shows the selected prefecture label on the trigger', () => {
    const okinawa = PREFECTURES.find((p) => p.value === 'Okinawa');
    render(
      <Header selectedPrefecture={okinawa} onSelectPrefecture={onSelectPrefecture} />
    );
    expect(
      screen.getByRole('button', { name: 'Select prefecture' })
    ).toHaveTextContent('Okinawa 沖縄');
  });

  // -------------------------------------------------------------------------
  // Dropdown open / close
  // -------------------------------------------------------------------------

  it('keeps the options list closed until the trigger is clicked', () => {
    render(<Header onSelectPrefecture={onSelectPrefecture} />);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('opens the options list when the trigger is clicked', async () => {
    const user = userEvent.setup();
    render(<Header onSelectPrefecture={onSelectPrefecture} />);

    await user.click(screen.getByRole('button', { name: 'Select prefecture' }));

    expect(screen.getByRole('listbox')).toBeInTheDocument();
    // 10 prefectures + the "All Japan" reset option.
    expect(screen.getAllByRole('option')).toHaveLength(PREFECTURES.length + 1);
  });

  it('reflects the open state via aria-expanded', async () => {
    const user = userEvent.setup();
    render(<Header onSelectPrefecture={onSelectPrefecture} />);

    const trigger = screen.getByRole('button', { name: 'Select prefecture' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
  });

  // -------------------------------------------------------------------------
  // Selection
  // -------------------------------------------------------------------------

  it('calls onSelectPrefecture with the chosen prefecture and closes the list', async () => {
    const user = userEvent.setup();
    render(<Header onSelectPrefecture={onSelectPrefecture} />);

    await user.click(screen.getByRole('button', { name: 'Select prefecture' }));
    await user.click(screen.getByRole('option', { name: 'Hakone 箱根' }));

    expect(onSelectPrefecture).toHaveBeenCalledWith(
      expect.objectContaining({ value: 'Hakone' })
    );
    // List closes after selection.
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('calls onSelectPrefecture with null when "All Japan" is chosen', async () => {
    const user = userEvent.setup();
    const okinawa = PREFECTURES.find((p) => p.value === 'Okinawa');
    render(
      <Header selectedPrefecture={okinawa} onSelectPrefecture={onSelectPrefecture} />
    );

    await user.click(screen.getByRole('button', { name: 'Select prefecture' }));
    // The first option is the "All Japan" reset entry.
    await user.click(screen.getByRole('option', { name: 'All Japan 日本' }));

    expect(onSelectPrefecture).toHaveBeenCalledWith(null);
  });

  it('marks the selected prefecture option as aria-selected', async () => {
    const user = userEvent.setup();
    const kyoto = PREFECTURES.find((p) => p.value === 'Kyoto');
    render(
      <Header selectedPrefecture={kyoto} onSelectPrefecture={onSelectPrefecture} />
    );

    await user.click(screen.getByRole('button', { name: 'Select prefecture' }));
    expect(screen.getByRole('option', { name: 'Kyoto 京都' })).toHaveAttribute(
      'aria-selected',
      'true'
    );
  });
});
