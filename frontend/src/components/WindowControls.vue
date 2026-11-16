<template><div v-if="desktop" class="native-controls"><button aria-label="最小化窗口" title="最小化" @click="invoke('minimize')"><Minus :size="18"/></button><button :aria-label="maximized?'还原窗口':'最大化窗口'" :title="maximized?'还原':'最大化'" @click="invoke('maximize')"><component :is="maximized?Copy:Square" :size="16"/></button><button class="close-window" aria-label="关闭窗口" title="关闭" @click="invoke('close')"><X :size="20"/></button></div></template>
<script setup lang="ts">
import { onMounted,onUnmounted,ref } from 'vue';
import { Minus,Copy,Square,X } from 'lucide-vue-next';
import '@/types/desktop';
const desktop=window.yuntongDesktop,maximized=ref(false);
let unsubscribe:(()=>void)|undefined;
async function invoke(action:'minimize'|'maximize'|'close'){try{await desktop?.[action]()}catch(error){console.error('窗口操作失败',error)}}
onMounted(async()=>{if(!desktop)return;maximized.value=(await desktop.windowState()).maximized;unsubscribe=desktop.onWindowState(state=>maximized.value=state.maximized)});
onUnmounted(()=>unsubscribe?.());
</script>
<style scoped>.native-controls{display:flex;align-self:stretch;margin-left:8px;-webkit-app-region:no-drag}.native-controls button{display:grid;place-items:center;width:46px;border:0;background:transparent;color:#202936}.native-controls button:hover{background:#e8eef7}.native-controls .close-window:hover{background:#df3545;color:white}</style>
