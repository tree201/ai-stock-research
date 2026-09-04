/** Theme tokens: the single source feeding both antd and the custom CSS. */

export type ThemeName = "light" | "dark";

/** CSS custom properties consumed by styles.css (keys map to `--<key>`). */
export const CSS_THEME_TOKENS: Record<ThemeName, Record<string, string>> = {
  light: {
    "paper": "#f2f4f1",
    "surface": "#ffffff",
    "surface-soft": "#f5f7f4",
    "surface-dim": "#f0f2ef",
    "ink": "#1c2421",
    "ink-strong": "#161d1a",
    "ink-soft": "#565869",
    "ink-muted": "#8e8ea0",
    "ink-body": "#2a3530",
    "line": "#dfe5e0",
    "line-soft": "#eef0ed",
    "signal": "#17856e",
    "signal-soft": "#e2f2ed",
    "rail-bg": "#f0f2ef",
    "rail-border": "#dde3de",
    "rail-hover": "#e3e9e5",
    "rail-selected": "#d8e3dd",
    "rail-text": "#2a3530",
    "rail-text-muted": "#75807b",
    "bubble-user": "#e9efec",
    "bubble-user-line": "#d9e3de",
  },
  dark: {
    "paper": "#101312",
    "surface": "#1e2321",
    "surface-soft": "#191e1c",
    "surface-dim": "#242a27",
    "ink": "#e6ebe8",
    "ink-strong": "#f2f5f3",
    "ink-soft": "#aeb8b2",
    "ink-muted": "#7d8783",
    "ink-body": "#c9d3cd",
    "line": "#2c332f",
    "line-soft": "#242a27",
    "signal": "#2fbf9a",
    "signal-soft": "#1d2b26",
    "rail-bg": "#161a18",
    "rail-border": "#2a302d",
    "rail-hover": "#293530",
    "rail-selected": "#2b3934",
    "rail-text": "#e6ebe8",
    "rail-text-muted": "#8d9b95",
    "bubble-user": "#242b28",
    "bubble-user-line": "#33403a",
  },
};

/** antd v5 tokens aligned with the CSS variables above. */
export const ANTD_THEME_TOKENS: Record<
  ThemeName,
  { colorPrimary: string; colorBgLayout: string; colorBgContainer: string; colorBgElevated: string; colorText: string; colorTextSecondary: string; colorTextTertiary: string; colorBorder: string; borderRadius: number }
> = {
  light: {
    colorPrimary: "#17856e",
    colorBgLayout: "#f2f4f1",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorText: "#1c2421",
    colorTextSecondary: "#565869",
    colorTextTertiary: "#8e8ea0",
    colorBorder: "#dfe5e0",
    borderRadius: 8,
  },
  dark: {
    colorPrimary: "#2fbf9a",
    colorBgLayout: "#101312",
    colorBgContainer: "#1e2321",
    colorBgElevated: "#232826",
    colorText: "#e6ebe8",
    colorTextSecondary: "#aeb8b2",
    colorTextTertiary: "#7d8783",
    colorBorder: "#2c332f",
    borderRadius: 8,
  },
};

export function initialTheme(): ThemeName {
  return localStorage.getItem("theme") === "dark" ? "dark" : "light";
}

/** Push CSS variables + data-theme onto <html> (call before first paint too). */
export function applyTheme(theme: ThemeName) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  Object.entries(CSS_THEME_TOKENS[theme]).forEach(([key, value]) => {
    root.style.setProperty(`--${key}`, value);
  });
}
