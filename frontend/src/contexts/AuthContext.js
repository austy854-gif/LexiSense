import { createContext, useContext, useState, useEffect } from 'react';
import { authAPI, billingAPI } from '../api';
import { FAILED_ACCESS, LOADING_ACCESS, normalizeAccess } from '../lib/access.mjs';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
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
    const token = localStorage.getItem('token');
    const savedUser = localStorage.getItem('user');
    
    if (token && savedUser) {
      setUser(JSON.parse(savedUser));
      authAPI.getMe()
        .then(async (res) => {
          setUser(res.data);
          localStorage.setItem('user', JSON.stringify(res.data));
          await refreshAccess();
        })
        .catch(() => {
          localStorage.removeItem('token');
          localStorage.removeItem('user');
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (email, password) => {
    const response = await authAPI.login(email, password);
    const { access_token, user: userData } = response.data;
    localStorage.setItem('token', access_token);
    localStorage.setItem('user', JSON.stringify(userData));
    setUser(userData);
    await refreshAccess();
    return userData;
  };

  const register = async (data) => {
    const response = await authAPI.register(data);
    const { access_token, user: userData } = response.data;
    localStorage.setItem('token', access_token);
    localStorage.setItem('user', JSON.stringify(userData));
    setUser(userData);
    await refreshAccess();
    return userData;
  };

  const logout = async () => {
    try {
      await authAPI.logout();
    } catch {
      // The local session is dropped either way. A 401 here means it was already dead.
    }
    localStorage.removeItem('token');
    localStorage.removeItem('user');
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
