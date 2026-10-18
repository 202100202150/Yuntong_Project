<template>
  <div class="page-content">
    <!-- 左右布局的父容器 -->
    <div class="cards-container">
      <!-- 左边 Camera 1 -->
      <div class="config-card">
        <h3>Camera 1</h3>
        <div class="form-group">
          <label>相机 IP</label>
          <input type="text" value="10.108.17.8" disabled class="locked-input" />
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>输出分辨率</label>
            <input type="text" value="相机原生" disabled class="locked-input" />
          </div>
          <div class="form-group">
            <label>预览分辨率</label>
            <input type="text" value="768x432" disabled class="locked-input" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>预览最高帧率</label>
            <input type="text" value="30 FPS" disabled class="locked-input" />
          </div>
          <div class="form-group">
            <label>AI 检测最高帧率</label>
            <input type="text" value="12.5 FPS" disabled class="locked-input" />
          </div>
        </div>
      </div>

      <!-- 右边 Camera 2 -->
      <div class="config-card">
        <h3>Camera 2</h3>
        <div class="form-group">
          <label>相机 IP</label>
          <input type="text" value="10.108.17.9" disabled class="locked-input" />
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>输出分辨率</label>
            <input type="text" value="相机原生" disabled class="locked-input" />
          </div>
          <div class="form-group">
            <label>预览分辨率</label>
            <input type="text" value="768x432" disabled class="locked-input" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>预览最高帧率</label>
            <input type="text" value="30 FPS" disabled class="locked-input" />
          </div>
          <div class="form-group">
            <label>AI 检测最高帧率</label>
            <input type="text" value="12.5 FPS" disabled class="locked-input" />
          </div>
        </div>
      </div>
    </div>

    <!-- 底部按钮区域 -->
    <div class="footer-area">
      <button class="save-btn" disabled>配置已锁定</button>
    </div>
  </div>
</template>

<script setup lang="ts">
// 不需要任何逻辑，纯展示
</script>

<style scoped>
/* 页面容器 */
.page-content {
  max-width: 1000px;
  margin: 0 auto;
  padding: 32px 24px;
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
}

h2 { margin-top: 0; font-size: 24px; color: var(--text-main); margin-bottom: 8px; }
.subtitle { color: var(--text-sub); margin: 0; font-size: 14px; }

/* 核心：Flex 左右并排 */
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

.form-group { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.form-row { display: flex; gap: 16px; }
.form-row .form-group { flex: 1; min-width: 0; }

label { font-size: 14px; font-weight: 500; color: var(--text-sub); }

input.locked-input {
  padding: 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 14px;
  background: var(--bg-gradient);
  color: var(--text-main);
  cursor: not-allowed;
  opacity: 0.8;
  width: 100%;
  box-sizing: border-box;
}

/* 底部按钮居中核心 */
.footer-area {
  display: flex;
  justify-content: center;
  width: 100%;
  margin-top: 12px;
}

.save-btn {
  background: #94a3b8;
  color: #fff;
  border: 0;
  padding: 12px 32px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: not-allowed;
  transition: all 0.2s;
}

@media (max-width: 768px) {
  .cards-container {
    flex-direction: column;
  }
}
</style>