import { createContext, useContext, useState, useEffect } from 'react';
import { authAPI, billingAPI } from '../api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [access, setAccess] = useState({ state: null, readOnly: false, message: null, capabilities: {} });

  const refreshAccess = async () => {
    try {
      const response = await billingAPI.getCapabilities();
      setAccess(response.data);
    } catch {
      // Keep the least-privileged UI state while billing is unavailable.
      setAccess({ state: 'unavailable', readOnly: true, message: 'Workspace access could not be verified.', capabilities: {} });
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
    setAccess({ state: null, readOnly: false, message: null, capabilities: {} });
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
