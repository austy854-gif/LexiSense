import { billingAPI } from '../api';
import { isBillingPath, loadBillingUiEnabled, resetBillingConfigCache } from './billingConfig';

jest.mock('../api', () => ({ billingAPI: { getConfig: jest.fn() } }));

beforeEach(() => {
  resetBillingConfigCache();
  billingAPI.getConfig.mockReset();
});

test('reads billingUiEnabled from the backend and caches it', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: true } });
  await expect(loadBillingUiEnabled()).resolves.toBe(true);
  await expect(loadBillingUiEnabled()).resolves.toBe(true);
  expect(billingAPI.getConfig).toHaveBeenCalledTimes(1);
});

test('anything but an explicit true is off', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: {} });
  await expect(loadBillingUiEnabled()).resolves.toBe(false);
  resetBillingConfigCache();
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: 'true' } });
  await expect(loadBillingUiEnabled()).resolves.toBe(false);
});

test('a failed request means off, and is retried next time', async () => {
  billingAPI.getConfig.mockRejectedValueOnce(new Error('Network Error'));
  await expect(loadBillingUiEnabled()).resolves.toBe(false);
  billingAPI.getConfig.mockResolvedValueOnce({ data: { billingUiEnabled: true } });
  await expect(loadBillingUiEnabled()).resolves.toBe(true);
  expect(billingAPI.getConfig).toHaveBeenCalledTimes(2);
});

test('billing paths', () => {
  expect(isBillingPath('/billing')).toBe(true);
  expect(isBillingPath('/contracts')).toBe(false);
});
