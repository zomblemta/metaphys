export async function api<T>(
  path: string,
  csrf: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...options.headers,
    },
  });
  if (!response.ok) {
    let data: { detail?: string; error?: { message?: string } } = {};
    try {
      data = await response.json();
    } catch {}
    throw new Error(data.error?.message || data.detail || "连接暂时不可用，请稍后重试。");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
