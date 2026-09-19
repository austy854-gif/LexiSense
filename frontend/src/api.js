import axios from 'axios';

const API_URL = process.env.REACT_APP_BACKEND_URL + '/api';

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// Auth API
export const authAPI = {
  login: (email, password) => api.post('/auth/login', { email, password }),
  register: (data) => api.post('/auth/register', data),
  getMe: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
};

// Contracts API
export const contractsAPI = {
  list: (params) => api.get('/contracts', { params }),
  get: (id) => api.get(`/contracts/${id}`),
  upload: (formData) => api.post('/contracts', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  bulkUpload: (formData) => api.post('/contracts/bulk', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  update: (id, data) => api.patch(`/contracts/${id}`, data),
  delete: (id) => api.delete(`/contracts/${id}`),
  chat: (id, question) => api.post(`/contracts/${id}/chat`, { question }),
};

// Team API
export const teamAPI = {
  listMembers: () => api.get('/team/members'),
  listInvitations: () => api.get('/team/invitations'),
  invite: (email, role) => api.post('/team/invite', { email, role }),
  cancelInvitation: (id) => api.delete(`/team/invitations/${id}`),
  updateRole: (memberId, role) => api.patch(`/team/members/${memberId}/role`, null, { params: { role } }),
  removeMember: (memberId) => api.delete(`/team/members/${memberId}`),
  acceptInvite: (token, password, firstName, lastName) => 
    api.post('/team/accept-invite', null, { params: { token, password, firstName, lastName } }),
};

// Dashboard API
export const dashboardAPI = {
  getStats: () => api.get('/dashboard/stats'),
  getActivity: () => api.get('/dashboard/activity'),
};

// Alerts API
export const alertsAPI = {
  getSettings: () => api.get('/alerts/settings'),
  updateSettings: (alertDays, emailEnabled) => 
    api.put('/alerts/settings', null, { params: { alertDays, emailEnabled } }),
  getExpiring: (days = 30) => api.get('/alerts/expiring', { params: { days } }),
  checkAndSend: () => api.post('/alerts/check-and-send'),
  getHistory: (limit = 50) => api.get('/alerts/history', { params: { limit } }),
};

// Contract Versions API
export const versionsAPI = {
  getVersions: (contractId) => api.get(`/contracts/${contractId}/versions`),
  getVersion: (contractId, versionNum) => api.get(`/contracts/${contractId}/versions/${versionNum}`),
  restore: (contractId, versionNum) => api.post(`/contracts/${contractId}/restore/${versionNum}`),
};

// Templates API
export const templatesAPI = {
  list: (params) => api.get('/templates', { params }),
  get: (id) => api.get(`/templates/${id}`),
  getDefault: () => api.get('/templates/default'),
  create: (data) => api.post('/templates', data),
  delete: (id) => api.delete(`/templates/${id}`),
};

// Export API
export const exportAPI = {
  contractPDF: (contractId) => api.get(`/export/contract/${contractId}/pdf`, { responseType: 'blob' }),
  analyticsPDF: () => api.get('/export/analytics/pdf', { responseType: 'blob' }),
};

// Analytics API
export const analyticsAPI = {
  getOverview: () => api.get('/analytics/overview'),
  compare: (id1, id2) => api.get(`/analytics/contracts/${id1}/compare/${id2}`),
};

// Workflow API
export const workflowAPI = {
  getStates: () => api.get('/contracts/workflow/states'),
  performAction: (contractId, action, comment) =>
    api.post(`/contracts/${contractId}/workflow/${action}`, null, { params: { comment } }),
  getHistory: (contractId) => api.get(`/contracts/${contractId}/workflow/history`),
};

// Notifications API
export const notificationsAPI = {
  list: (limit = 30) => api.get('/notifications', { params: { limit } }),
  unreadCount: () => api.get('/notifications/unread-count'),
  markRead: (id) => api.post(`/notifications/${id}/read`),
  markAllRead: () => api.post('/notifications/read-all'),
};

// Audit API
export const auditAPI = {
  list: (params) => api.get('/audit', { params }),
};

// Billing API
export const billingAPI = {
  getSubscription: () => api.get('/billing/subscription'),
  createCheckout: (priceId, successUrl, cancelUrl) => api.post('/billing/checkout', { priceId, successUrl, cancelUrl }),
  createPortal: (returnUrl) => api.post('/billing/portal', { returnUrl }),
  getPlans: () => api.get('/billing/plans'),
};

// Agentic AI API
export const agenticApi = {
  // Agents
  createAgent: (data) => api.post('/agentic/agents', data),
  listAgents: (params) => api.get('/agentic/agents', { params }),
  getAgent: (id) => api.get(`/agentic/agents/${id}`),
  updateAgent: (id, data) => api.patch(`/agentic/agents/${id}`, data),
  deleteAgent: (id) => api.delete(`/agentic/agents/${id}`),
  runAgent: (id) => api.post(`/agentic/agents/${id}/run`),
  getAgentExecutions: (id) => api.get(`/agentic/agents/${id}/executions`),

  // Risk Scoring
  assessRisk: (contractId, force = false) => api.post('/agentic/risk/assess', { contractId, force_refresh: force }),
  getRiskAssessment: (contractId) => api.get(`/agentic/risk/assessments/${contractId}`),
  listRiskAssessments: (params) => api.get('/agentic/risk/assessments', { params }),
  refreshStaleRisks: () => api.post('/agentic/risk/refresh-stale'),
  bulkAssessRisks: () => api.post('/agentic/risk/bulk-assess'),

  // Playbooks
  createPlaybook: (data) => api.post('/agentic/playbooks', data),
  listPlaybooks: () => api.get('/agentic/playbooks'),
  getPlaybook: (id) => api.get(`/agentic/playbooks/${id}`),
  updatePlaybook: (id, data) => api.patch(`/agentic/playbooks/${id}`, data),
  deletePlaybook: (id) => api.delete(`/agentic/playbooks/${id}`),
  analyzeWithPlaybook: (contractId, playbookId, autoApply = false) =>
    api.post('/agentic/playbooks/analyze', { contractId, playbookId, auto_apply: autoApply }),
  getRedlineSession: (sessionId) => api.get(`/agentic/playbooks/sessions/${sessionId}`),
  applyRedlines: (sessionId, suggestionIds) =>
    api.post(`/agentic/playbooks/sessions/${sessionId}/apply`, { suggestionIds }),

  // Intake
  listIntakes: (params) => api.get('/agentic/intake', { params }),
  getIntake: (id) => api.get(`/agentic/intake/${id}`),
  processIntake: (id, data) => api.post(`/agentic/intake/${id}/process`, data),
  retryFailedIntakes: () => api.post('/agentic/intake/retry-failed'),

  // Obligations
  extractObligations: (contractId, force = false) => api.post('/agentic/obligations/extract', { contractId, force_refresh: force }),
  listObligations: (params) => api.get('/agentic/obligations', { params }),
  getObligation: (id) => api.get(`/agentic/obligations/${id}`),
  updateObligationStatus: (id, data) => api.patch(`/agentic/obligations/${id}/status`, data),
  listObligationAlerts: (params) => api.get('/agentic/obligations/alerts', { params }),
  acknowledgeAlert: (id) => api.post(`/agentic/obligations/alerts/${id}/acknowledge`),
  bulkExtractObligations: () => api.post('/agentic/obligations/bulk-extract'),
};

export default api;
