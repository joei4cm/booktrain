import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

export type Theme = "light" | "dark" | "auto";

const STORAGE_KEY = "booktran-theme";

interface ThemeCtx {
  theme: Theme;
  setTheme: (t: Theme) => void;
  /** 当前实际生效的模式（auto 解析后的结果） */
  resolved: "light" | "dark";
}

const Ctx = createContext<ThemeCtx>({ theme: "auto", setTheme: () => {}, resolved: "light" });

function getStored(): Theme {
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "light" || v === "dark" || v === "auto" ? v : "auto";
}

function systemDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getStored);
  const [systemPref, setSystemPref] = useState(systemDark);

  // 监听系统主题变化
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => { setSystemPref(systemDark()); };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const resolved = theme === "auto" ? (systemPref ? "dark" : "light") : theme;

  // 把解析结果写到 <html data-theme>
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    localStorage.setItem(STORAGE_KEY, t);
  }, []);

  return <Ctx.Provider value={{ theme, setTheme, resolved }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  return useContext(Ctx);
}
