import {createRouter,createWebHistory} from 'vue-router';
import MainLayout from '@/layouts/MainLayout.vue';
import {useAuthStore} from '@/stores/auth';
const router=createRouter({history:createWebHistory(),routes:[
 {path:'/login',component:()=>import('@/views/LoginView.vue'),meta:{title:'登录'}},
 {path:'/',component:MainLayout,redirect:'/workbench',children:[{path:'workbench',component:()=>import('@/views/DualCameraView.vue'),meta:{title:'实时监控',permission:'monitor'}}]},
 {path:'/:pathMatch(.*)*',redirect:'/workbench'}
]});
router.beforeEach(async to=>{const auth=useAuthStore();if(!auth.initialized){try{await auth.refresh()}catch{if(to.path!=='/login')return'/login'}}if(to.path!=='/login'&&!auth.user)return{path:'/login',query:{redirect:to.fullPath}};if(to.path==='/login'&&auth.user)return'/workbench';document.title=String(to.meta.title||'实时监控')+' · 云瞳 AIRSPACE'});
export default router;
