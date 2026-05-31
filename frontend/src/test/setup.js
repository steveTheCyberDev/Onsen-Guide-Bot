// Global test setup — runs before every test file.
// Adds jest-dom matchers (toBeInTheDocument, toHaveTextContent, etc.).
import '@testing-library/jest-dom';

// jsdom does not implement scrollIntoView — stub it so components that call it
// (e.g. MessageList auto-scroll) don't throw in tests.
if (typeof window !== 'undefined') {
  window.HTMLElement.prototype.scrollIntoView = function () {};
}
