/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['"Noto Serif JP"', 'Georgia', 'serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        japanese: ['"Noto Sans JP"', 'sans-serif'],
      },
      colors: {
        primary: '#C9533A',
        secondary: '#2D6A4F',
        accent: '#E9C46A',
        background: '#FAF7F2',
        'chat-bg': '#F0EBE3',
        'text-main': '#2C2C2C',
      },
      animation: {
        'slide-up': 'slideUp 0.2s ease-out',
      },
      keyframes: {
        slideUp: {
          '0%': { transform: 'translateY(100%)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};
