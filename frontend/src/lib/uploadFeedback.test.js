import {
  BULK_ALL_FAILED_MESSAGE,
  UPLOAD_ANALYZED_MESSAGE,
  UPLOAD_NOT_ANALYZED_MESSAGE,
  bulkUploadFeedback,
  singleUploadFeedback,
} from './uploadFeedback';

describe('singleUploadFeedback', () => {
  test('claims analysis only when it succeeded', () => {
    expect(singleUploadFeedback({ aiAnalysisStatus: 'success' })).toEqual({
      type: 'success',
      message: UPLOAD_ANALYZED_MESSAGE,
    });
  });

  test.each([['failed'], [undefined], [null], ['pending']])(
    'status %p is reported as saved but not analyzed',
    (status) => {
      const fb = singleUploadFeedback({ aiAnalysisStatus: status });
      expect(fb.type).toBe('warning');
      expect(fb.message).toBe(UPLOAD_NOT_ANALYZED_MESSAGE);
      expect(fb.message).not.toMatch(/analyzed successfully/i);
    }
  );

  test('a missing response body does not claim success', () => {
    expect(singleUploadFeedback(undefined).type).toBe('warning');
  });
});

describe('bulkUploadFeedback', () => {
  test('all succeeded: success toast without "0 failed"', () => {
    const fb = bulkUploadFeedback({ successful: 3, failed: 0 });
    expect(fb).toEqual({ type: 'success', message: '3 contracts uploaded.' });
  });

  test('singular wording for one file', () => {
    expect(bulkUploadFeedback({ successful: 1, failed: 0 }).message).toBe('1 contract uploaded.');
  });

  test('some failed: warning with both counts', () => {
    const fb = bulkUploadFeedback({ successful: 2, failed: 1 });
    expect(fb.type).toBe('warning');
    expect(fb.message).toBe("2 uploaded, 1 didn't. Check each is a PDF under 10 MB.");
  });

  test('all failed: error toast, never success', () => {
    const fb = bulkUploadFeedback({
      successful: 0,
      failed: 2,
      results: { failed: [{ error: 'Bad type' }, { error: 'Too big' }] },
    });
    expect(fb).toEqual({ type: 'error', message: BULK_ALL_FAILED_MESSAGE });
  });

  test('all failed for one shared reason: that reason is shown', () => {
    const reason = 'Your free trial has ended. Upgrade to keep adding contracts.';
    const fb = bulkUploadFeedback({
      successful: 0,
      failed: 2,
      results: { failed: [{ error: reason }, { error: reason }] },
    });
    expect(fb).toEqual({ type: 'error', message: `None of those files uploaded. ${reason}` });
  });

  test('empty or malformed body is an error, not a green toast', () => {
    expect(bulkUploadFeedback({}).type).toBe('error');
    expect(bulkUploadFeedback(undefined).type).toBe('error');
    expect(bulkUploadFeedback({ successful: '0', failed: '0' }).type).toBe('error');
  });
});
