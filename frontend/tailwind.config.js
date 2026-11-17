/** Same design tokens that base.html used to pass to the Play CDN (see AGENTS.md).
 *
 * Light theme (QA-053): every colour family the templates use (slate, red, amber, ...) and the surface / muted / accent
 * tokens read CSS variables, so the SAME class names (text-slate-100, bg-surface, ...) render in both themes. The
 * variables are emitted below: the values of the default Tailwind palette on :root (= the dark theme the app always
 * had) and an adjusted set under :root[data-theme="light"]. An element with data-palette="default" (the invoice paper,
 * which is white in both themes) always gets the default palette back.
 */
const plugin = require('tailwindcss/plugin');
const twColors = require('tailwindcss/colors');

const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const FAMILIES = ['slate', 'red', 'amber', 'yellow', 'emerald', 'green', 'blue', 'purple'];

// slate, reversed and tuned for a white page: 100 is the main text colour, 400 the secondary text, 700 the borders
const LIGHT_SLATE = {
  50: '#020617', 100: '#0f172a', 200: '#1e293b', 300: '#334155', 400: '#475569', 500: '#64748b',
  600: '#94a3b8', 700: '#cbd5e1', 800: '#e2e8f0', 900: '#f1f5f9', 950: '#f8fafc',
};
// chromatic families: the light shades used for text on the dark theme (200-400) become dark enough to read on white and
// on a pale tint of the same colour (800+), 500 and 600 become 700 (text and solid buttons with white text), and the dark
// shades (backgrounds on the dark theme) become pale
const LIGHT_SHADE_SOURCE = { 50: 950, 100: 950, 200: 900, 300: 800, 400: 800, 500: 700, 600: 700, 700: 700, 800: 200, 900: 100, 950: 50 };

const triplet = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
};

const defaultVars = {};
const lightVars = {};
for (const family of FAMILIES) {
  for (const shade of SHADES) {
    defaultVars[`--c-${family}-${shade}`] = triplet(twColors[family][shade]);
    // yellow is paler than the other families: its text/tint shades go one step darker
    const source = family === 'yellow' && [500, 600, 700].includes(shade) ? 800 : LIGHT_SHADE_SOURCE[shade];
    lightVars[`--c-${family}-${shade}`] = triplet(family === 'slate' ? LIGHT_SLATE[shade] : twColors[family][source]);
  }
}
Object.assign(defaultVars, {
  '--c-surface': triplet('#0F172A'), '--c-surface-card': triplet('#1E293B'), '--c-muted': triplet('#94A3B8'),
  '--c-accent': triplet('#F59E0B'), '--c-accent-dark': triplet('#D97706'),
});
Object.assign(lightVars, {
  // page: slate-100, cards: white; muted 7.6:1 on white; accent = amber-700 (5.0:1 on white, white-ish text on it)
  '--c-surface': triplet('#F1F5F9'), '--c-surface-card': triplet('#FFFFFF'), '--c-muted': triplet('#475569'),
  '--c-accent': triplet('#B45309'), '--c-accent-dark': triplet('#92400E'),
});

const themed = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;
const family = (name) => Object.fromEntries(SHADES.map((s) => [s, themed(`${name}-${s}`)]));

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
        ...Object.fromEntries(FAMILIES.map((f) => [f, family(f)])),
        brand: { DEFAULT: '#1B3A6B', light: '#2A5298' },
        accent: { DEFAULT: themed('accent'), dark: themed('accent-dark') },
        surface: { DEFAULT: themed('surface'), card: themed('surface-card') },
        muted: themed('muted'), // dark: #94A3B8 (was #64748B: 3.7:1 on the page background, below WCAG AA) (QA-033)
      },
      fontFamily: {
        sans: ['DM Sans', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({
        ':root, [data-palette="default"]': { ...defaultVars, 'color-scheme': 'dark' },
        ':root[data-theme="light"]': { ...lightVars, 'color-scheme': 'light' },
        '[data-palette="default"]': { 'color-scheme': 'light' },
      });
    }),
  ],
};
