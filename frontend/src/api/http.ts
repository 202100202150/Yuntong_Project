import axios from 'axios';

const baseURL = (import.meta.env.VITE_API_BASE as string) || '/api';

export const http = axios.create({
  baseURL,
  timeout: 15_000,
});

http.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error?.response?.status === 401 && !error.config?.url?.startsWith('/auth/')) window.dispatchEvent(new Event('yuntong:session-expired'));
    const message =
      error?.response?.data?.error ??
      error?.message ??
      '网络请求失败';
    return Promise.reject(new Error(message));
  },
);