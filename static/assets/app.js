/**
 * Nodus 公共工具函数
 * 所有页面共享的通用 JS 函数，避免重复代码
 */

// ── HTML 转义（防 XSS）────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── 侧边栏切换 ────────────────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('show');
}

// ── 时间格式化（简短）────────────────────────────────────────────────────
function formatTimeShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

// ── 时间格式化（标准）────────────────────────────────────────────────────
function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ── 相对时间（x mins ago）───────────────────────────────────────────────
function getTimeAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now - d;
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} mins ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

// ── 统一 API 请求（带错误处理）────────────────────────────────────────────
async function apiFetch(url, options = {}) {
  try {
    const resp = await fetch(url, { credentials: 'same-origin', ...options });
    const data = await resp.json();
    if (!data.ok) {
      console.warn(`API 错误 [${url}]:`, data.error?.message || '未知错误');
    }
    return data;
  } catch (err) {
    console.error(`请求失败 [${url}]:`, err);
    return { ok: false, error: { message: '网络错误，请重试' } };
  }
}

// ── 统一认证检查 ──────────────────────────────────────────────────────────
async function checkAuth() {
  const data = await apiFetch('/api/auth/me');
  if (!data.ok) {
    window.location.href = '/login.html';
    return null;
  }
  return data.data;
}

// ── 显示加载状态 ──────────────────────────────────────────────────────────
function showLoading(el, text = '加载中...') {
  if (typeof el === 'string') el = document.getElementById(el);
  if (el) el.innerHTML = `<p style="color:var(--text-light);font-size:.88rem;text-align:center;padding:16px;">${text}</p>`;
}

// ── 显示错误状态 ──────────────────────────────────────────────────────────
function showError(el, text = '加载失败，请刷新重试') {
  if (typeof el === 'string') el = document.getElementById(el);
  if (el) el.innerHTML = `<p style="color:var(--accent);font-size:.88rem;text-align:center;padding:16px;">${text}</p>`;
}

// ── 显示空状态 ────────────────────────────────────────────────────────────
function showEmpty(el, text = '暂无数据') {
  if (typeof el === 'string') el = document.getElementById(el);
  if (el) el.innerHTML = `<p style="color:var(--text-light);font-size:.88rem;text-align:center;padding:16px;">${text}</p>`;
}
