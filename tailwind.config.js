/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
    './portfolio_app/**/*.py',
  ],
  theme: {
    extend: {
      colors: {
        navy: '#1D3557',
        blue: '#457B9D',
        teal: '#A8DADC',
        cream: '#F1FAEE',
        // Object form (with DEFAULT) keeps Tailwind's built-in red-50..950
        // shades while allowing bare `bg-red` / `text-red`.
        red: { DEFAULT: '#E63946' },
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
