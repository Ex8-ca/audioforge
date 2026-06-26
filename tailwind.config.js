/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#f43f5e',
        'primary-hover': '#e11d48',
        'primary-glow': 'rgba(244,63,94,0.3)',
        surface: '#111827',
        'surface-2': '#1f2937',
        'surface-3': '#374151',
        border: '#4b5563',
        accent: '#8b5cf6',
      },
      fontFamily: { sans: ['Inter', 'sans-serif'] },
    },
  },
  plugins: [],
};