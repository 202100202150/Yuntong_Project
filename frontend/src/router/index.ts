import{createRouter,createWebHistory}from'vue-router';import MainLayout from '@/layouts/MainLayout.vue';import{useAuthStore}from'@/stores/auth';
const router=createRouter({history:createWebHistory(),routes:[{path:'/login',component:()=>import('@/views/LoginView.vue'),meta:{title:'登录'}},{path:'/',component:MainLayout,redirect:'/home',children:[
{path:'home',component:()=>import('@/views/HomeView.vue'),meta:{title:'运行总览',permission:'monitor'}},
{path:'workbench',component:()=>import('@/views/DualCameraView.vue'),meta:{title:'实时监控',permission:'monitor'}},
{path:'events',component:()=>import('@/views/EventsView.vue'),meta:{title:'事件中心',permission:'events'}},
{path:'playback',component:()=>import('@/views/PlaybackView.vue'),meta:{title:'录像回放',permission:'playback'}},{path:'library',redirect:'/playback'},
{path:'devices',component:()=>import('@/views/DevicesView.vue'),meta:{title:'设备管理',permission:'devices'}},
{path:'calibration',redirect:'/devices'},
{path:'models',component:()=>import('@/views/ModelsView.vue'),meta:{title:'检测模型',permission:'models'}},
{path:'settings',component:()=>import('@/views/SettingsView.vue'),meta:{title:'系统设置',permission:'settings'}},{path:'about',redirect:'/home'}]},
{path:'/:pathMatch(.*)*',redirect:'/home'}]});
router.beforeEach(async to=>{const auth=useAuthStore();if(!auth.initialized){try{await auth.refresh()}catch{if(to.path!=='/login')return'/login'}}
if(to.path!=='/login'&&!auth.user)return{path:'/login',query:{redirect:to.fullPath}};if(to.path==='/login'&&auth.user)return'/home';if(to.meta.permission&&!auth.can(String(to.meta.permission))&&!(to.path==='/settings'&&to.query.tab==='audit'&&auth.can('audit')))return'/home';document.title=String(to.meta.title||'云瞳')+' · 云瞳 AIRSPACE'});export default router;
