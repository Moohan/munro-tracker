import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        peat: "#1c1713",
        heather: "#6c5f4d",
        mist: "#dce6e4",
        pine: "#26483f",
        granite: "#9ea8ab",
        lichen: "#b2c572",
        ember: "#d7613b",
      },
      boxShadow: {
        ridge: "0 18px 60px rgba(16, 24, 21, 0.16)",
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "Segoe UI", "sans-serif"],
        display: ["Fraunces", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
