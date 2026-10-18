<template>
  <div class="app-wrapper">
    <router-view />
  </div>
</template>

<script setup lang="ts">
</script>

<style>
/* 确保 html, body 铺满全屏，消除默认边距 */
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overflow: hidden; /* 防止出现网页级双滚动条 */
}

#app {
  width: 100%;
  height: 100%;
}

/* 包裹 router-view 的容器，强制撑满 */
.app-wrapper {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}
</style>