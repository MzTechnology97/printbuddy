// The page that sent the user to the login, so a link (e.g. from Seller Hub) still works after it.
// sessionStorage: it survives the OIDC round trip in the same tab.
const KEY = 'login_return_to';

export function saveLoginReturn(path: string): void {
  try {
    sessionStorage.setItem(KEY, path);
  } catch {
    // Storage blocked: the login goes home, as before.
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
