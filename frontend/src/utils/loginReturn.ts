// The page that sent the user to the login, so a link (e.g. from Seller Hub) still works after it.
// sessionStorage: it survives the OIDC round trip in the same tab.
const KEY = 'login_return_to';
// Set by a logout: this page is on its way to /login and nothing on it is to come back to (the
// protected page re-renders without a user before the reload). A new page load starts clear.
let loggedOut = false;

export function saveLoginReturn(path: string): void {
  if (loggedOut) return;
  try {
    sessionStorage.setItem(KEY, path);
  } catch {
    // Storage blocked: the login goes home, as before.
  }
}

/** Forget the saved page: a failed login, or a logout (``loggingOut``: no page saved after it either). */
export function clearLoginReturn({ loggingOut = false } = {}): void {
  loggedOut ||= loggingOut;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Storage blocked: nothing saved.
  }
}

/** The saved page, once; home when there is none or it isn't a page of this app. */
export function takeLoginReturn(): string {
  let path: string | null = null;
  try {
    path = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
  } catch {
    // Storage blocked: home.
  }
  const inApp = !!path && path.startsWith('/') && !path.startsWith('//') && !path.startsWith('/\\');
  return inApp && !path!.startsWith('/login') ? path! : '/';
}
