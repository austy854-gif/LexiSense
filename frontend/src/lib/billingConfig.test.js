import { billingAPI } from '../api';
import { isBillingPath, loadBillingUiEnabled, resetBillingConfigCache } from './billingConfig';

jest.mock('../api', () => ({ __esModule: true, default: {}, billingAPI: { getConfig: jest.fn() } }));

beforeEach(() => { resetBillingConfigCache(); billingAPI.getConfig.mockReset(); });

test('on only when the server says true', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: true } });
  expect(await loadBillingUiEnabled()).toBe(true);
});

test.each([{ billingUiEnabled: false }, {}, { billingUiEnabled: 'true' }])('off for %p', async (data) => {
  billingAPI.getConfig.mockResolvedValue({ data });
  expect(await loadBillingUiEnabled()).toBe(false);
});

test('request failure means off, and retries next time', async () => {
  billingAPI.getConfig.mockRejectedValueOnce(new Error('down'));
  expect(await loadBillingUiEnabled()).toBe(false);
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: true } });
  expect(await loadBillingUiEnabled()).toBe(true);
});

test('billing paths', () => {
  expect(isBillingPath('/billing')).toBe(true);
  expect(isBillingPath('/contracts')).toBe(false);
});
