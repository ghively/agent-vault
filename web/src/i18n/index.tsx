import React, { createContext, useContext } from "react";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// Initialize i18next with minimal translations
i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: {} },
    },
    lng: "en",
    fallbackLng: "en",
    interpolation: {
      escapeValue: false,
    },
  });

interface I18nContextType {
  locale: string;
  t: (key: string, params?: Record<string, unknown>) => string;
}

const I18nContext = createContext<I18nContextType | undefined>(undefined);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const t = (key: string, params?: Record<string, unknown>): string => {
    const translated = i18n.t(key, params);
    return translated;
  };

  return (
    <I18nContext.Provider value={{ locale: "en", t }}>
      {children}
    </I18nContext.Provider>
  );
}

// useT hook for functional components
export function useT(): (key: string, params?: Record<string, unknown>) => string {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useT must be used within I18nProvider");
  }
  return context.t;
}

// T component for JSX
export function T({ k, params }: { k: string; params?: Record<string, unknown> }) {
  const t = useT();
  return <>{t(k, params)}</>;
}
