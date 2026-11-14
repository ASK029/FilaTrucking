/** Same design tokens that base.html used to pass to the Play CDN (see AGENTS.md). */
module.exports = {
  content: [
    '../FilaTrucking/**/*.html',
    '../FilaTrucking/**/*.py',        // classes added by TailwindFormMixin etc.
    '../FilaTrucking/**/static/**/*.js',
    '!../FilaTrucking/static/vendor/**',
    '!../FilaTrucking/staticfiles/**',
    '!../FilaTrucking/media/**',
  ],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: '#1B3A6B', light: '#2A5298' },
        accent: { DEFAULT: '#F59E0B', dark: '#D97706' },
        surface: { DEFAULT: '#0F172A', card: '#1E293B' },
        muted: '#94A3B8', // was #64748B: 3.7:1 on the page background, below the WCAG AA minimum of 4.5:1 (QA-033)
      },
      fontFamily: {
        sans: ['DM Sans', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
};
