import { createRouter, createWebHistory } from 'vue-router';
import MainLayout from '@/layouts/MainLayout.vue';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '系统登录' }
    },
    {
      path: '/',
      component: MainLayout,
      redirect: '/home',
      children: [
        {
          path: 'home',
          name: 'home',
          component: () => import('@/views/HomeView.vue'),
          meta: { title: '系统首页' }
        },
        {
          path: 'workbench',
          name: 'workbench',
          component: () => import('@/views/DualCameraView.vue'),
          meta: { title: '双摄像机检测工作台' }
        },
        {
          path: 'devices',
          name: 'devices',
          component: () => import('@/views/DeviceManage.vue'),
          meta: { title: '设备管理配置' }
        },
        {
          path: 'models',
          name: 'models',
          component: () => import('@/views/ModelConfig.vue'),
          meta: { title: '模型配置' }
        },
        {
          path: 'library',
          name: 'library',
          component: () => import('@/views/MediaLibrary.vue'),
          meta: { title: '历史回放库' }
        },
        // 👇 加上下面这一段
        {
          path: 'about',
          name: 'about',
          component: () => import('@/views/AboutView.vue'),
          meta: { title: '关于我们' }
        }
      ]
    }
  ],
});

// 全局路由守卫
router.beforeEach((to, _from, next) => {
  const isLoggedIn = localStorage.getItem('isLoggedIn') === 'true';

  if (to.path !== '/login' && !isLoggedIn) {
    next('/login');
  } else if (to.path === '/login' && isLoggedIn) {
    next('/home');
  } else {
    next();
  }
});

export default router;