import { createContext, useContext, useState, useEffect, useRef } from 'react';
import { authAPI, billingAPI } from '../api';
import { FAILED_ACCESS, LOADING_ACCESS, normalizeAccess } from '../lib/access.mjs';

const AuthContext = createContext(null);

const USER_KEY = 'user';
const TOKEN_KEY = 'token';

/**
 * Read a previously stored auth user from localStorage.
 * Never throws. Treats null/empty/'undefined'/'null'/invalid JSON/non-objects
 * as no user, and clears inconsistent session keys.
 */
export function readStoredUser() {
  let raw;
  try {
    raw = localStorage.getItem(USER_KEY);
  } catch {
    return null;
  }

  if (raw == null || raw === '' || raw === 'undefined' || raw === 'null') {
    clearSessionKeys();
    return null;
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    clearSessionKeys();
    return null;
  }

  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    clearSessionKeys();
    return null;
  }

  return parsed;
}

function clearSessionKeys() {
  try {
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore storage failures
  }
}

function readToken() {
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token == null || token === '' || token === 'undefined' || token === 'null') {
      return null;
    }
    return token;
  } catch {
    return null;
  }
}

/** Persist session only when values are defined; otherwise remove the key. */
export function persistSession(accessToken, userData) {
  try {
    if (typeof accessToken === 'string' && accessToken && accessToken !== 'undefined') {
      localStorage.setItem(TOKEN_KEY, accessToken);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }

    if (userData != null && typeof userData === 'object' && !Array.isArray(userData)) {
      localStorage.setItem(USER_KEY, JSON.stringify(userData));
    } else {
      localStorage.removeItem(USER_KEY);
    }
  } catch {
    // ignore storage failures
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  // Bumped by login/register/logout so a stale startup getMe() can't
  // overwrite or clear a session established after restore began.
  const sessionGen = useRef(0);
  // Fail closed: every capability is false until /billing/capabilities answers.
  const [access, setAccess] = useState(LOADING_ACCESS);

  const refreshAccess = async () => {
    try {
      const response = await billingAPI.getCapabilities();
      setAccess(normalizeAccess(response?.data));
    } catch {
      // Least-privileged state: all capabilities false, workspace read-only.
      setAccess(FAILED_ACCESS);
    }
  };

  useEffect(() => {
    const token = readToken();
    const savedUser = readStoredUser();

    // readStoredUser already cleared bad user+token; if only token was missing,
    // treat as logged out. If user was bad, keys are already cleared.
    if (token && savedUser) {
      setUser(savedUser);
      const restoreGen = sessionGen.current;
      const isCurrent = () => sessionGen.current === restoreGen;
      authAPI.getMe()
        .then(async (res) => {
          if (!isCurrent()) return;
          const me = res?.data;
          if (me != null && typeof me === 'object' && !Array.isArray(me)) {
            setUser(me);
            persistSession(token, me);
          }
          await refreshAccess();
        })
        .catch(() => {
          if (!isCurrent()) return;
          clearSessionKeys();
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      // Inconsistent: token without usable user (or vice versa) → clear.
      clearSessionKeys();
      setUser(null);
      setLoading(false);
    }
  }, []);

  const login = async (email, password) => {
    const response = await authAPI.login(email, password);
    sessionGen.current += 1;
    setLoading(false);
    const { access_token, user: userData } = response.data || {};
    persistSession(access_token, userData);
    setUser(userData && typeof userData === 'object' ? userData : null);
    await refreshAccess();
    return userData;
  };

  const register = async (data) => {
    const response = await authAPI.register(data);
    sessionGen.current += 1;
    setLoading(false);
    const { access_token, user: userData } = response.data || {};
    persistSession(access_token, userData);
    setUser(userData && typeof userData === 'object' ? userData : null);
    await refreshAccess();
    return userData;
  };

  const logout = async () => {
    sessionGen.current += 1;
    try {
      await authAPI.logout();
    } catch {
      // The local session is dropped either way. A 401 here means it was already dead.
    }
    clearSessionKeys();
    setUser(null);
    setAccess(LOADING_ACCESS);
  };

  return (
    <AuthContext.Provider value={{ user, loading, access, capabilities: access.capabilities, refreshAccess, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
