import { create } from "zustand";

interface AuthState {
  token: string | null;
  setToken: (t: string) => void;
  clearToken: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: sessionStorage.getItem("vault_token"),
  setToken: (t) => {
    sessionStorage.setItem("vault_token", t);
    set({ token: t });
  },
  clearToken: () => {
    sessionStorage.removeItem("vault_token");
    set({ token: null });
  },
}));
