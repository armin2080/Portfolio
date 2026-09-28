/** @type {import('tailwindcss').Config} */

// Colours resolve to CSS custom properties rather than fixed hex values, so an
// admin-editable Theme can restyle the site without a rebuild. The raw channels
// are stored as "R G B" and wrapped in rgb(... / <alpha-value>) so Tailwind's
// opacity modifiers (bg-primary bg-opacity-80) keep working.
const token = (role) => `rgb(var(--c-${role}) / <alpha-value>)`;

module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
    './portfolio_app/**/*.py',
  ],
  theme: {
    extend: {
      colors: {
        // Themed roles — edited per Theme in the admin.
        primary: token('primary'),
        secondary: token('secondary'),
        accent: token('accent'),
        emphasis: token('emphasis'),
        page: token('page'),
        surface: token('surface'),
        inverse: token('inverse'),
        // Separate from `primary` on purpose: a single token cannot be both a
        // dark surface (nav in dark mode) and readable dark-mode heading text.
        heading: token('heading'),
        ink: token('ink'),
        muted: token('muted'),
        border: token('border'),

        // Deliberately NOT themed: form validation states must stay red in
        // every palette. Declaring only DEFAULT leaves Tailwind's built-in
        // red-50..950 scale intact for bg-red-100 / text-red-700 and friends.
        red: { DEFAULT: '#E63946' },
      },
      fontFamily: {
        sans: 'var(--font-body)',
        heading: 'var(--font-heading)',
      },
    },
  },
  plugins: [],
};
