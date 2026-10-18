<template>
  <div class="login-container">
    <!-- 背景图层：直接内嵌了你的科技感 SVG -->
    <div class="bg-wrapper">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 800" preserveAspectRatio="xMidYMid slice" width="100%" height="100%">
        <!-- 1. 深空背景渐变 -->
        <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#0b1121" />
          <stop offset="50%" stop-color="#0f172a" />
          <stop offset="100%" stop-color="#1e1b4b" />
        </linearGradient>

        <radialGradient id="aurora1" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#06b6d4" stop-opacity="0.2" />
          <stop offset="100%" stop-color="#06b6d4" stop-opacity="0" />
        </radialGradient>

        <radialGradient id="aurora2" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.15" />
          <stop offset="100%" stop-color="#8b5cf6" stop-opacity="0" />
        </radialGradient>

        <linearGradient id="textGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#ffffff" />
          <stop offset="100%" stop-color="#67e8f9" />
        </linearGradient>

        <linearGradient id="darkGlass" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#1e293b" stop-opacity="0.9" />
          <stop offset="100%" stop-color="#0f172a" stop-opacity="0.4" />
        </linearGradient>

        <linearGradient id="glowLine" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#06b6d4" />
          <stop offset="100%" stop-color="#8b5cf6" />
        </linearGradient>

        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>

        <filter id="strongGlow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="12" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>

        <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
          <path d="M 60 0 L 0 0 0 60" fill="none" stroke="#1e293b" stroke-width="1" opacity="0.6"/>
        </pattern>

        <!-- 绘制背景 -->
        <rect width="1600" height="800" fill="url(#bgGrad)" />
        <rect width="1600" height="800" fill="url(#grid)" />
        <circle cx="1200" cy="400" r="500" fill="url(#aurora1)" filter="url(#strongGlow)" />
        <circle cx="800" cy="200" r="400" fill="url(#aurora2)" filter="url(#strongGlow)" />

        <circle cx="200" cy="150" r="1.5" fill="#fff" opacity="0.4" />
        <circle cx="450" cy="700" r="2" fill="#06b6d4" opacity="0.6" filter="url(#glow)" />
        <circle cx="1400" cy="200" r="1" fill="#fff" opacity="0.3" />
        <circle cx="1500" cy="650" r="2" fill="#8b5cf6" opacity="0.5" filter="url(#glow)" />

        <!-- 左侧文字 (原图文字，但在登录页中通常被遮罩，保留作视觉基调) -->
        <g transform="translate(120, 260)">
          <text x="0" y="0" font-family="system-ui, -apple-system, sans-serif" font-size="96" font-weight="900" fill="url(#textGrad)" letter-spacing="4">云瞳 AIRSPACE</text>
          <g transform="translate(0, 50)">
            <rect x="0" y="0" width="60" height="8" rx="4" fill="#06b6d4" filter="url(#glow)" />
            <rect x="70" y="0" width="200" height="8" rx="4" fill="#334155" />
            <rect x="280" y="0" width="20" height="8" rx="4" fill="#8b5cf6" filter="url(#glow)" />
          </g>
          <text x="0" y="150" font-family="system-ui, -apple-system, sans-serif" font-size="44" font-weight="800" fill="#e2e8f0" letter-spacing="6">校园低空安全监测平台</text>
          <text x="0" y="210" font-family="monospace, system-ui, sans-serif" font-size="22" font-weight="600" fill="#94a3b8" letter-spacing="8">3D AIRSPACE · LIVE MONITORING</text>
        </g>

        <!-- 右侧科技元素 -->
        <g transform="translate(850, 400)">
          <ellipse cx="250" cy="150" rx="400" ry="120" fill="none" stroke="#06b6d4" stroke-width="1.5" opacity="0.2" stroke-dasharray="15 15"/>
          <ellipse cx="250" cy="150" rx="320" ry="90" fill="none" stroke="#8b5cf6" stroke-width="1" opacity="0.3" />
          <ellipse cx="250" cy="150" rx="240" ry="60" fill="none" stroke="#06b6d4" stroke-width="2" opacity="0.1" filter="url(#glow)"/>

          <g transform="translate(20, 80)">
            <polygon points="0,80 80,40 160,80 160,160 80,200 0,160" fill="url(#darkGlass)" stroke="#06b6d4" stroke-width="2"/>
            <polygon points="80,40 160,80 160,160 80,200" fill="#0891b2" opacity="0.2"/>
            <line x1="30" y1="100" x2="70" y2="80" stroke="#67e8f9" stroke-width="3" filter="url(#glow)"/>
            <line x1="30" y1="120" x2="50" y2="110" stroke="#67e8f9" stroke-width="3" filter="url(#glow)"/>
          </g>

          <g transform="translate(300, 0)">
            <polygon points="0,60 100,20 200,60 200,140 100,180 0,140" fill="url(#darkGlass)" stroke="#8b5cf6" stroke-width="2"/>
            <polygon points="100,20 200,60 200,140 100,180" fill="#7c3aed" opacity="0.2"/>
            <polygon points="100,20 150,40 150,70 100,50" fill="none" stroke="#a78bfa" stroke-width="2" filter="url(#glow)"/>
          </g>

          <g transform="translate(200, 140)">
            <polygon points="0,40 40,20 80,40 80,120 40,140 0,120" fill="url(#darkGlass)" stroke="#38bdf8" stroke-width="2"/>
            <polygon points="40,20 80,40 80,120 40,140" fill="#0284c7" opacity="0.3"/>
            <ellipse cx="40" cy="40" rx="60" ry="20" fill="none" stroke="#06b6d4" stroke-width="2" opacity="0.8" filter="url(#glow)"/>
            <ellipse cx="40" cy="40" rx="120" ry="40" fill="none" stroke="#06b6d4" stroke-width="1" opacity="0.3" />
            <path d="M 40,40 L 40,-40" stroke="#67e8f9" stroke-width="2" stroke-dasharray="4 4" filter="url(#glow)"/>
            <circle cx="40" cy="-40" r="4" fill="#fff" filter="url(#glow)"/>
          </g>

          <g transform="translate(420, 40)">
            <rect width="140" height="70" rx="8" fill="#1e293b" opacity="0.8" stroke="#06b6d4" stroke-width="1.5" filter="url(#glow)"/>
            <rect x="15" y="15" width="90" height="6" rx="3" fill="#06b6d4" />
            <rect x="15" y="30" width="60" height="6" rx="3" fill="#3b82f6" />
            <rect x="15" y="45" width="40" height="6" rx="3" fill="#8b5cf6" />
            <path d="M 0,70 L -40,110" stroke="#06b6d4" stroke-width="2" opacity="0.6" />
            <circle cx="-40" cy="110" r="3" fill="#06b6d4" filter="url(#glow)"/>
          </g>

          <g transform="translate(60, -60)">
            <rect width="110" height="40" rx="6" fill="#1e293b" opacity="0.9" stroke="#8b5cf6" stroke-width="1.5" filter="url(#glow)"/>
            <circle cx="20" cy="20" r="5" fill="#10b981" filter="url(#glow)"/>
            <text x="35" y="25" font-family="monospace" font-size="14" font-weight="bold" fill="#e2e8f0" letter-spacing="1">ONLINE</text>
          </g>

          <g transform="translate(0, 160)">
            <line x1="0" y1="0" x2="40" y2="0" stroke="#ef4444" stroke-width="2" filter="url(#glow)"/>
            <line x1="0" y1="0" x2="-20" y2="10" stroke="#10b981" stroke-width="2" filter="url(#glow)"/>
            <line x1="0" y1="0" x2="0" y2="-30" stroke="#3b82f6" stroke-width="2" filter="url(#glow)"/>
            <circle cx="0" cy="0" r="4" fill="#fff" filter="url(#glow)"/>
          </g>

          <g transform="translate(120, -100) rotate(-15)">
            <polygon points="0,0 60,120 -60,120" fill="url(#glowLine)" opacity="0.1" />
            <circle cx="0" cy="0" r="12" fill="#0f172a" stroke="#06b6d4" stroke-width="2" filter="url(#glow)"/>
            <line x1="0" y1="0" x2="-30" y2="-25" stroke="#334155" stroke-width="4"/>
            <line x1="0" y1="0" x2="30" y2="-25" stroke="#334155" stroke-width="4"/>
            <line x1="0" y1="0" x2="-25" y2="30" stroke="#334155" stroke-width="4"/>
            <line x1="0" y1="0" x2="25" y2="30" stroke="#334155" stroke-width="4"/>
            <ellipse cx="-30" cy="-25" rx="20" ry="5" fill="none" stroke="#67e8f9" stroke-width="2" filter="url(#glow)"/>
            <ellipse cx="30" cy="-25" rx="20" ry="5" fill="none" stroke="#67e8f9" stroke-width="2" filter="url(#glow)"/>
            <ellipse cx="-25" cy="30" rx="20" ry="5" fill="none" stroke="#67e8f9" stroke-width="2" filter="url(#glow)"/>
            <ellipse cx="25" cy="30" rx="20" ry="5" fill="none" stroke="#67e8f9" stroke-width="2" filter="url(#glow)"/>
          </g>

          <g transform="translate(480, -80) rotate(-10)">
            <path d="M -100,12 L 0,0" stroke="#8b5cf6" stroke-width="4" opacity="0.8" stroke-linecap="round" filter="url(#glow)"/>
            <path d="M -150,18 L 0,0" stroke="#c084fc" stroke-width="1.5" opacity="0.4" stroke-linecap="round" />
            <path d="M 0,12 L 40,6 L 80,0 L 100,-5 L 60,-6 L 20,0 Z" fill="#1e293b" stroke="#8b5cf6" stroke-width="2"/>
            <path d="M 40,6 L 50,-25 L 70,-25 L 55,2 Z" fill="url(#glowLine)" filter="url(#glow)"/>
            <path d="M 70,0 L 75,25 L 60,25 L 55,2 Z" fill="#4c1d95"/>
            <circle cx="0" cy="12" r="4" fill="#fff" filter="url(#glow)"/>
          </g>
        </g>
      </svg>
    </div>

    <!-- 登录表单区域 -->
    <div class="login-box">
      <div class="login-header">
        <h1>系统登录</h1>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="input-group">
          <label>账号</label>
          <input
            type="text"
            v-model="username"
            placeholder="请输入账号"
            required
            autocomplete="username"
          />
        </div>

        <div class="input-group">
          <label>密码</label>
          <input
            type="password"
            v-model="password"
            placeholder="请输入密码"
            required
            autocomplete="current-password"
          />
        </div>

        <div class="error-msg" v-if="errorMsg">{{ errorMsg }}</div>

        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? '登录中...' : '登 录' }}
        </button>
      </form>

      <div class="login-footer">
        © 2026 云瞳系统 · 仅限内部测试使用
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();

const username = ref('');
const password = ref('');
const errorMsg = ref('');
const loading = ref(false);

// 写死的账号密码
const VALID_USER = 'test';
const VALID_PASS = '904';

const handleLogin = () => {
  errorMsg.value = '';

  if (!username.value || !password.value) {
    errorMsg.value = '请输入账号和密码';
    return;
  }

  loading.value = true;

  // 模拟网络请求延迟
  setTimeout(() => {
    if (username.value === VALID_USER && password.value === VALID_PASS) {
      // 登录成功，保存状态（可选）
      localStorage.setItem('isLoggedIn', 'true');
      // 跳转到工作台或首页
      router.push('/home');
    } else {
      errorMsg.value = '账号或密码错误，请重新输入';
      loading.value = false;
    }
  }, 600);
};
</script>

<style scoped>
/* 整个页面容器 */
.login-container {
  position: relative;
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: flex-end; /* 将登录框靠右放置，不遮挡左侧大标题 */
  padding-right: 15vw;
  box-sizing: border-box;
  overflow: hidden;
  background-color: #0b1121;
}

/* 背景图层铺满 */
.bg-wrapper {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none; /* 防止背景干扰输入框点击 */
}

/* 登录框 - 暗黑玻璃拟物风格 */
.login-box {
  position: relative;
  z-index: 1;
  width: 420px;
  background: rgba(30, 41, 59, 0.4); /* 半透明深色底 */
  backdrop-filter: blur(16px); /* 核心：毛玻璃模糊效果 */
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  padding: 48px 40px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5),
              inset 0 1px 0 rgba(255, 255, 255, 0.1);
  animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

/* 入场动画 */
@keyframes slideUp {
  from { opacity: 0; transform: translateY(30px); }
  to { opacity: 1; transform: translateY(0); }
}

.login-header {
  text-align: center;
  margin-bottom: 40px;
}

.login-header h1 {
  font-size: 28px;
  font-weight: 800;
  color: #ffffff;
  margin: 0 0 8px 0;
  letter-spacing: 2px;
}

.login-header p {
  font-size: 14px;
  color: #94a3b8;
  margin: 0;
  letter-spacing: 1px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-group label {
  font-size: 13px;
  font-weight: 600;
  color: #cbd5e1;
  letter-spacing: 1px;
}

.input-group input {
  width: 100%;
  padding: 12px 16px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: #ffffff;
  font-size: 14px;
  outline: none;
  transition: all 0.3s ease;
  box-sizing: border-box;
}

.input-group input::placeholder {
  color: #64748b;
}

/* 聚焦时的发光边框 */
.input-group input:focus {
  border-color: #06b6d4;
  box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.2);
  background: rgba(15, 23, 42, 0.8);
}

.error-msg {
  color: #ef4444;
  font-size: 13px;
  text-align: center;
  margin-top: -10px;
}

.login-btn {
  margin-top: 8px;
  width: 100%;
  padding: 14px;
  background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 4px;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 15px rgba(6, 182, 212, 0.3);
}

.login-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(6, 182, 212, 0.5);
  filter: brightness(1.1);
}

.login-btn:active:not(:disabled) {
  transform: translateY(0);
}

.login-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.login-footer {
  text-align: center;
  margin-top: 32px;
  font-size: 12px;
  color: #475569;
}
</style>