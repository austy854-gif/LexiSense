/**
 * Decide what the user is told after an upload. Kept pure (no React, no
 * toast library) so every branch is unit-tested.
 *
 * Each function returns `{ type, message }`, where `type` is the sonner toast
 * variant to use: 'success' | 'warning' | 'error'.
 */

export const UPLOAD_ANALYZED_MESSAGE = 'Contract uploaded. Your analysis is ready.';
export const UPLOAD_NOT_ANALYZED_MESSAGE =
  "Contract saved. The analysis didn't finish, so re-upload it to try again.";
export const BULK_ALL_FAILED_MESSAGE =
  'None of those files uploaded. Check each is a PDF or .txt file under 10 MB.';

/** Outcome of a single upload, from the created contract in the response. */
export function singleUploadFeedback(contract) {
  if (contract && contract.aiAnalysisStatus === 'success') {
    return { type: 'success', message: UPLOAD_ANALYZED_MESSAGE };
  }
  // 'failed', or a missing/unknown status: the contract is saved, but we
  // cannot claim it was analyzed.
  return { type: 'warning', message: UPLOAD_NOT_ANALYZED_MESSAGE };
}

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/**
 * Outcome of a bulk upload, from the `/contracts/bulk` response body:
 * `{ successful: number, failed: number, results: { failed: [{ error }] } }`.
 */
export function bulkUploadFeedback(data) {
  const successful = count(data && data.successful);
  const failed = count(data && data.failed);

  if (successful > 0 && failed === 0) {
    return {
      type: 'success',
      message: `${successful} ${successful === 1 ? 'contract' : 'contracts'} uploaded.`,
    };
  }

  if (successful === 0) {
    // When every file failed for the same reason (e.g. the trial has ended),
    // that reason is more useful than the generic file-format hint.
    const reasons = ((data && data.results && data.results.failed) || [])
      .map((f) => (f && typeof f.error === 'string' ? f.error.trim() : ''))
      .filter(Boolean);
    const unique = [...new Set(reasons)];
    if (failed > 0 && unique.length === 1 && reasons.length === failed) {
      return { type: 'error', message: `None of those files uploaded. ${unique[0]}` };
    }
    return { type: 'error', message: BULK_ALL_FAILED_MESSAGE };
  }

  return {
    type: 'warning',
    message: `${successful} uploaded, ${failed} didn't. Check each is a PDF or .txt file under 10 MB.`,
  };
}
