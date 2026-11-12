<template>
  <div class="page-content">
    <div class="header-area">
      <h2>云瞳 AIRSPACE · 校园低空安全监测平台</h2>
    </div>

    <div class="cards-container">
      <!-- 左侧：系统信息 -->
      <div class="config-card">
        <h3>系统信息</h3>
        <div class="info-row">
          <span class="label">系统名称：</span>
          <span class="value">云瞳智能双摄检测平台</span>
        </div>
        <div class="info-row">
          <span class="label">当前版本：</span>
          <span class="value">v2.0.1 (2026 旗舰版)</span>
        </div>
        <div class="info-row">
          <span class="label">算法架构：</span>
          <span class="value">YOLO + TensorRT + 3D 空间匹配</span>
        </div>
        <div class="info-row">
          <span class="label">底层支持：</span>
          <span class="value">Python / Node.js / Vue 3</span>
        </div>
      </div>

      <!-- 右侧：开发者/团队信息 -->
      <div class="config-card">
        <h3>技术团队</h3>
        <div class="info-row">
          <span class="label">研发团队：</span>
          <span class="value">云瞳核心技术实验室</span>
        </div>
        <div class="info-row">
          <span class="label">联系邮箱：</span>
          <span class="value">support@yuntong-airspace.com</span>
        </div>
        <div class="info-row">
          <span class="label">官网地址：</span>
          <span class="value">www.yuntong-airspace.com</span>
        </div>
        <div class="info-row">
          <span class="label">版权声明：</span>
          <span class="value">© 2026 云瞳系统 · 保留所有权利</span>
        </div>
      </div>
    </div>

    <div class="footer-area">
      <p class="desc">致力于打造更安全、更智能的校园低空环境</p>
    </div>
  </div>
</template>

<script setup lang="ts">
// 纯展示页面，无额外逻辑
</script>

<style scoped>
.page-content {
  max-width: 1000px;
  margin: 0 auto;
  padding: 32px 24px;
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
}

.header-area {
  margin-bottom: 24px;
}

h2 { margin-top: 0; font-size: 24px; color: var(--text-main); margin-bottom: 8px; }
.subtitle { color: var(--text-sub); margin: 0; font-size: 14px; }

.cards-container {
  display: flex;
  gap: 24px;
  align-items: stretch;
  margin-bottom: 24px;
}

.config-card {
  flex: 1;
  min-width: 0;
  background: var(--header-bg);
  padding: 24px;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  border: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
}

h3 {
  margin-top: 0;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 12px;
  margin-bottom: 20px;
  font-size: 16px;
  color: var(--text-main);
}

.info-row {
  display: flex;
  margin-bottom: 16px;
  font-size: 14px;
  line-height: 1.5;
}

.info-row:last-child {
  margin-bottom: 0;
}

.label {
  color: var(--text-sub);
  width: 100px;
  flex-shrink: 0;
}

.value {
  color: var(--text-main);
  font-weight: 500;
}

.footer-area {
  text-align: center;
  margin-top: 32px;
}

.desc {
  color: var(--text-sub);
  font-size: 13px;
  margin: 0;
}

@media (max-width: 768px) {
  .cards-container {
    flex-direction: column;
  }
}
</style>
