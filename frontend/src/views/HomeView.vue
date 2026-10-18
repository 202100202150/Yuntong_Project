<template>
  <div class="home-container">
    <!-- 替换为你真实的图片文件名，比如 home-bg.jpg -->
    <img src="@/assets/bg.png" alt="云瞳系统首页" class="home-image" />
  </div>
</template>

<script setup lang="ts">
// 首页不需要逻辑，纯展示图片
</script>

<style scoped>
.home-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--header-bg); /* 使用主题背景色，即使图片没铺满也不会突兀 */
  overflow: hidden;
}

.home-image {
  width: 100%;
  height: 100%;
  /* object-fit: contain; 保持图片完整比例，不裁剪（如果希望铺满不留黑边，可以改成 cover） */
  object-fit: contain;
  display: block;
}
</style>