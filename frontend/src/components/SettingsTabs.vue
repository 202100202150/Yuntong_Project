<template>
<nav class="settings-tabs" aria-label="系统配置">
  <template v-if="auth.can('models')"><span v-if="active==='models'" class="active current-tab" aria-current="page">检测模型</span><router-link v-else to="/models">检测模型</router-link></template>
  <template v-if="auth.can('users')"><span v-if="active==='users'" class="active current-tab" aria-current="page">用户与权限</span><router-link v-else to="/settings">用户与权限</router-link></template>
  <template v-if="auth.can('audit')"><span v-if="active==='audit'" class="active current-tab" aria-current="page">操作日志</span><router-link v-else to="/settings?tab=audit">操作日志</router-link></template>
</nav>
</template>
<script setup lang="ts">
import {useAuthStore} from '@/stores/auth';
const auth=useAuthStore();
defineProps<{active:string}>();
</script>
<style scoped>
.settings-tabs {
  display: flex;
  align-items: stretch;
  gap: 12px;
  min-height: 47px;
  margin-bottom: 22px;
  border-bottom: 1px solid #d9e0eb;
}
.settings-tabs a,
.settings-tabs button,
.settings-tabs .current-tab {
  display: flex;
  align-items: center;
  position: relative;
  padding: 0 17px;
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  font: inherit;
  font-size: 18px;
  font-weight: 600;
  color: #111827;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
}
.settings-tabs > * + *::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 50%;
  width: 1px;
  height: 21px;
  background: #dbe2ee;
  transform: translateY(-50%);
}
.settings-tabs .active {
  color: #0868ff;
  border-bottom-color: #0868ff;
}
.settings-tabs .current-tab { cursor: default; }
</style>
