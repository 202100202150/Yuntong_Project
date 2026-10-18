<template>
  <div class="dashboard-layout" :class="{ 'dark-mode': isDarkMode }" ref="layoutRef">
    <aside class="sidebar">
      <div class="logo">
        <img src="@/assets/logo.png" alt="云瞳图标" class="logo-img" />
        <span>云瞳系统</span>
      </div>
      <nav class="menu">
        <router-link to="/home" class="menu-item" active-class="active">
          <Home :size="18" /><span>首页</span>
        </router-link>

        <router-link to="/workbench" class="menu-item" active-class="active">
          <LayoutDashboard :size="18" /><span>工作台</span>
        </router-link>

        <router-link to="/devices" class="menu-item" active-class="active">
          <Camera :size="18" /><span>设备管理</span>
        </router-link>

        <router-link to="/models" class="menu-item" active-class="active">
          <Cpu :size="18" /><span>模型配置</span>
        </router-link>

        <router-link to="/library" class="menu-item" active-class="active">
          <FolderOpen :size="18" /><span>历史回放</span>
        </router-link>

        <!-- 👇 新增关于我们菜单 -->
        <router-link to="/about" class="menu-item" active-class="active">
          <Info :size="18" /><span>关于我们</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">© 2026 云瞳系统</div>
    </aside>

    <main class="main-content">
      <header class="top-header">
        <div class="header-left">
          <h1>{{ pageTitle }}</h1>
        </div>
        <div class="header-right-actions">
          <!-- 👇 Teleport 的挂载点，子页面的按钮会显示在这里 -->
          <div id="global-header-actions"></div>

          <button class="icon-btn" @click="toggleTheme" title="切换主题">
            <component :is="isDarkMode ? Sun : Moon" :size="18" />
          </button>
          <button class="action-btn" @click="toggleAppFullscreen">
            <Maximize :size="16" /> 全屏应用
          </button>
        </div>
      </header>

      <div class="page-content-wrapper">
        <router-view />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
// 👇 引入 Info 图标
import { Home, LayoutDashboard, Camera, Maximize, Sun, Moon, Cpu, FolderOpen, Info } from 'lucide-vue-next';

const route = useRoute();
const isDarkMode = ref(false);
const layoutRef = ref<HTMLElement | null>(null);

const pageTitle = computed(() => {
  return (route.meta?.title as string) || '工作台';
});

const toggleTheme = () => (isDarkMode.value = !isDarkMode.value);

const toggleAppFullscreen = async () => {
  if (!layoutRef.value) return;
  try {
    if (!document.fullscreenElement) await layoutRef.value.requestFullscreen();
    else await document.exitFullscreen();
  } catch (err) {
    console.warn('全屏失败', err);
  }
};
</script>

<style scoped>
.dashboard-layout {
  --bg-gradient: linear-gradient(135deg, #f0f7ff 0%, #ffffff 100%);
  --header-bg: #ffffff;
  --text-main: #1e293b;
  --text-sub: #64748b;
  --border-color: #cbd5e1;

  display: flex;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background-color: #f8fafc;
}

.dashboard-layout.dark-mode {
  --bg-gradient: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  --header-bg: #0f172a;
  --text-main: #f8fafc;
  --text-sub: #94a3b8;
  --border-color: #334155;
  background-color: #0f172a;
}

/* 侧边栏 */
.sidebar {
  width: 180px;
  flex-shrink: 0;
  background: #1e293b;
  border-right: 1px solid #334155;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.logo {
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  font-weight: 700;
  color: #fff;
  border-bottom: 1px solid #334155;
  flex-shrink: 0;
}
.logo-img { width: 28px; height: 28px; margin-right: 12px; border-radius: 6px; }
.menu { padding: 16px 12px; flex: 1; overflow-y: auto; }
.menu-item {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; text-decoration: none; color: #94a3b8;
  border-radius: 8px; margin-bottom: 4px; transition: all 0.2s;
}
.menu-item:hover { background: #334155; color: #f8fafc; }
.menu-item.active { background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); color: #ffffff; font-weight: 600; }
.sidebar-footer { padding: 24px 16px; text-align: center; font-size: 12px; color: #64748b; flex-shrink: 0; }

/* 主内容区 */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  overflow: hidden;
}

/* 顶栏 */
.top-header {
  height: 64px;
  flex-shrink: 0;
  background: var(--header-bg);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  box-sizing: border-box;
}
.header-left h1 { font-size: 16px; font-weight: 600; margin: 0; color: var(--text-main); }
.header-right-actions {
  display: flex;
  gap: 16px;
  align-items: center;
}
.action-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px; background: #1e293b; border: 1px solid #334155;
  border-radius: 8px; color: #fff; cursor: pointer;
}
.icon-btn {
  display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; background: transparent;
  border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-sub); cursor: pointer;
}

/* 路由渲染容器 */
.page-content-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-gradient);
}

.page-content-wrapper > :deep(*) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
</style>