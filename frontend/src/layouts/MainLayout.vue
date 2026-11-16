<template><div class="realtime-shell">
<header class="realtime-titlebar"><BrandMark/><span class="title-divider"/><span class="workspace-title">{{paneTitle}}</span><div class="titlebar-spacer"/><span class="connection-label" :class="{online:online>0}"><i/>{{online>0?'同步中':monitor.error?'服务未连接':'等待视频连接'}}</span><time>{{clock}}</time><div class="session-menu" ref="accountElement"><button class="icon-button account-trigger" aria-label="账号菜单" :title="auth.user?.displayName" :aria-expanded="accountOpen" @click="accountOpen=!accountOpen"><UserRound :size="18"/></button><div v-if="accountOpen" class="account-popover"><strong>{{auth.user?.displayName}}</strong><small>{{auth.user?.username}}</small><button @click="logout" :disabled="loggingOut"><LogOut :size="16"/>{{loggingOut?'正在退出…':'退出登录'}}</button></div></div><WindowControls/></header>
<main class="realtime-main"><RouterView/></main><ToastStack :toasts="camera.toasts"/>
</div></template>
<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref} from 'vue';
import {useRoute,useRouter} from 'vue-router';
import {UserRound,LogOut} from 'lucide-vue-next';
import BrandMark from '@/components/BrandMark.vue';
import WindowControls from '@/components/WindowControls.vue';
import ToastStack from '@/components/ToastStack.vue';
import {useAuthStore} from '@/stores/auth';
import {useCameraStore} from '@/stores/camera';
import {useMonitorStore} from '@/stores/monitor';
const auth=useAuthStore(),camera=useCameraStore(),monitor=useMonitorStore(),route=useRoute(),router=useRouter();
const accountOpen=ref(false),loggingOut=ref(false),accountElement=ref<HTMLElement|null>(null),clock=ref('');
const online=computed(()=>monitor.devices.filter(d=>d.runtime?.state==='streaming'&&d.runtime?.frameAvailable).length);
const paneTitle=computed(()=>route.query.pane==='scene'?'三维态势':route.query.pane==='video'?'视频监控':'实时监控');
let stopped=false,statusTimer:ReturnType<typeof setTimeout>|undefined,deviceTimer:ReturnType<typeof setTimeout>|undefined,clockTimer:ReturnType<typeof setInterval>|undefined;
async function statusPoll(){if(stopped)return;await camera.refreshStatus();if(!stopped)statusTimer=setTimeout(statusPoll,camera.status.detecting?400:2000)}
async function devicesPoll(){if(stopped)return;await monitor.refresh();if(!stopped)deviceTimer=setTimeout(devicesPoll,2000)}
function closeOutside(event:PointerEvent){if(!accountElement.value?.contains(event.target as Node))accountOpen.value=false}
async function logout(){if(loggingOut.value)return;loggingOut.value=true;try{await auth.logout();await router.replace('/login')}catch(error){camera.toast((error as Error).message,'error')}finally{loggingOut.value=false}}
onMounted(()=>{void statusPoll();void devicesPoll();void monitor.loadSceneConfig().catch(error=>camera.toast(error.message,'error'));const tick=()=>clock.value=new Date().toLocaleTimeString('zh-CN',{hour12:false});tick();clockTimer=setInterval(tick,1000);document.addEventListener('pointerdown',closeOutside)});
onUnmounted(()=>{stopped=true;clearTimeout(statusTimer);clearTimeout(deviceTimer);clearInterval(clockTimer);document.removeEventListener('pointerdown',closeOutside)});
</script>
<style scoped>
.realtime-shell{height:100dvh;min-height:420px;display:flex;flex-direction:column;background:linear-gradient(140deg,#fff,#f5f8fe);overflow:hidden}.realtime-titlebar{height:56px;min-height:56px;display:flex;align-items:center;gap:20px;border-bottom:1px solid #dfe5f1;padding-left:26px;background:#ffffffc9;-webkit-app-region:drag}.title-divider{height:24px;width:1px;background:#9aa5b8;margin:0 1px}.workspace-title{font-size:21px;color:#141b27;font-weight:650;white-space:nowrap}.titlebar-spacer{flex:1}.connection-label{font-size:15px;display:flex;gap:9px;align-items:center;white-space:nowrap;color:#728197}.connection-label i{width:11px;height:11px;background:#9ba8bb;border-radius:50%}.connection-label.online{color:#172031}.connection-label.online i{background:#22b834}.realtime-titlebar time{font-variant-numeric:tabular-nums;border-left:1px solid #c8d3e5;padding-left:20px;font-size:16px;color:#172031}.realtime-main{flex:1;min-height:0;overflow:hidden}.session-menu{position:relative;-webkit-app-region:no-drag;margin-right:8px}.account-popover{position:absolute;z-index:100;right:0;top:45px;width:220px;background:#fff;border:1px solid #d5deed;border-radius:10px;box-shadow:0 10px 36px #1e3d6420;padding:16px;display:flex;flex-direction:column;gap:9px}.account-popover small{color:#74829a}.account-popover button{display:flex;align-items:center;gap:10px;background:#f6f8fc;border:1px solid #e0e6f0;border-radius:6px;padding:9px;margin-top:8px;color:#243249}.realtime-titlebar :deep(.brand-mark){font-size:23px}.realtime-titlebar :deep(.brand-mark b){font-size:23px}.realtime-titlebar :deep(.brand-mark svg){width:42px}@media(max-width:1000px){.realtime-titlebar{gap:12px;padding-left:14px}.workspace-title{font-size:18px}.connection-label{font-size:12px}.realtime-titlebar time{font-size:13px;padding-left:12px}.realtime-titlebar :deep(.brand-mark),.realtime-titlebar :deep(.brand-mark b){font-size:20px}}@media(max-width:650px){.workspace-title,.title-divider,.connection-label{display:none}.realtime-titlebar{gap:8px}.realtime-titlebar time{border:0;padding:0;font-size:12px}}
</style>
