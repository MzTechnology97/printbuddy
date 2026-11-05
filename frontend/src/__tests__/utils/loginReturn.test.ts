import { describe, it, expect, beforeEach } from 'vitest';
import { saveLoginReturn, takeLoginReturn } from '../../utils/loginReturn';

describe('loginReturn', () => {
  beforeEach(() => sessionStorage.clear());

  it('comes back once to the page that asked for the login, with its query', () => {
    saveLoginReturn('/farm/print-plan?accoda=C-0099');
    expect(takeLoginReturn()).toBe('/farm/print-plan?accoda=C-0099');
    expect(takeLoginReturn()).toBe('/');
  });

  it('goes home for anything that is not a page of this app', () => {
    for (const path of ['https://evil.test/', '//evil.test/x', String.raw`/\evil.test`, '/login', 'farm']) {
      saveLoginReturn(path);
      expect(takeLoginReturn()).toBe('/');
    }
  });
});
